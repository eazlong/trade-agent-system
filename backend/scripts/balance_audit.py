#!/usr/bin/env python
"""只读审计：解释「余额变了，但系统里没有订单」。

用法（在 txy 的 backend 容器里跑，不写库、不下单）：

    ssh txy 'sudo -n docker exec -i trade_agent_sys-backend-1 python -' < backend/scripts/balance_audit.py

    # 只查某个账户 / 指定回溯天数
    ... < balance_audit.py  # 然后设环境变量
    AUDIT_DAYS=90 AUDIT_ACCOUNT=<uuid> ...

它回答什么
----------
本地 ``orders`` 表按设计只记「本系统发出过一张单」。而合约账户的 USDT 至少被四类
流动改变，其中**三类根本不产生 Order 行**：

    成交（平仓盈亏 / 吃单手续费）   部分有行（手续费从来没有独立列）
    资金费 FUNDING_FEE              没有行
    划转 TRANSFER / 充值 / 提现     没有行
    未实现盈亏                      不是流水，但改「可用余额」

所以「余额变了却没有订单」在多数情况下不是账丢了，而是这两类流动此前**没有任何读面**
（全仓没有一处调用 /fapi/v1/income）。本脚本就是那个口。

输出三段
--------
1. 现状：各账户余额（含细分）、持仓
2. 全历史流水按类型合计 + 与当前余额对平（``流水合计 - 当前余额`` 应为 0）
3. 逐单对账：交易所有、本地没有的订单；本地有、pnl 与交易所不一致的
"""

from __future__ import annotations

import asyncio
import os
import time
from collections import defaultdict
from decimal import Decimal, InvalidOperation

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings.dev")

import django  # noqa: E402

django.setup()

from apps.core.db_utils import db_async  # noqa: E402
from apps.exchange.models import ExchangeAccount  # noqa: E402
from apps.trading.models import Order  # noqa: E402
from apps.trading.pending_reconcile import build_account_adapter  # noqa: E402

#: 币安 `/fapi/v1/income` 不给 `startTime` 只回最近 7 天，所以起点必须显式给。
#: 默认从 2025-01-01 起，足够覆盖这个系统的全部历史。
DEFAULT_START_MS = 1735689600000
DAYS = int(os.environ.get("AUDIT_DAYS", "0") or 0)
ONLY_ACCOUNT = os.environ.get("AUDIT_ACCOUNT") or None


def _ts(ms) -> str:
    """毫秒 → ``YYYY-MM-DD HH:MM``。**必须带 tz**，否则本机时区会静默偏移。"""
    if ms is None:
        return "-"
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.gmtime(int(ms) / 1000))
    except (TypeError, ValueError, OSError):
        return "-"


def _d(value) -> Decimal:
    try:
        return Decimal(str(value or 0))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0")


async def _fetch_all_income(adapter, start_ms: int, end_ms: int) -> list[dict]:
    """分页拉全流水。区间超 ``limit`` 时靠「上一页最后一笔 time +1ms」推进游标。"""
    out: list[dict] = []
    seen: set[tuple] = set()
    cursor = start_ms
    while True:
        resp = await adapter._request(
            "GET",
            "/fapi/v1/income",
            {"startTime": int(cursor), "endTime": int(end_ms), "limit": 1000},
        )
        resp.raise_for_status()
        page = resp.json() or []
        if not isinstance(page, list):
            raise RuntimeError(f"income 返回非列表：{type(page).__name__}")
        for row in page:
            # 去重键必须是**整行**：`tranId` 在币安不唯一（同一笔成交的
            # REALIZED_PNL 与 COMMISSION 共用它），单用会把手续费当重复丢掉
            # ——实测会少算一半手续费，且差额表出现一个查不出原因的黑洞。
            key = (
                str(row.get("tranId") or ""),
                str(row.get("incomeType") or ""),
                str(row.get("time") or ""),
                str(row.get("income") or ""),
                str(row.get("orderId") or ""),
                str(row.get("tradeId") or ""),
                str(row.get("symbol") or ""),
            )
            if key in seen:
                continue
            seen.add(key)
            out.append(row)
        if len(page) < 1000:
            break
        last = page[-1].get("time")
        if last is None:
            break
        nxt = int(last) + 1
        if nxt <= cursor:
            break
        cursor = nxt
    return out


async def audit(acc) -> None:
    print(f"\n{'=' * 70}")
    print(f"{acc.label} | testnet={acc.testnet} | active={acc.is_active}")
    print(f"account_id = {acc.id}")

    adapter = await build_account_adapter(acc)
    if adapter is None:
        print("  无可用适配器（缺密钥 / 无该交易所）")
        return

    try:
        # ── 1. 现状 ────────────────────────────────────────────────
        try:
            bal = await adapter.get_balance()
            shown = ", ".join(f"{k}={v}" for k, v in bal.items() if v != 0)
            print(f"  余额: {shown or '（全部为 0）'}")
        except Exception as exc:  # noqa: BLE001
            print(f"  余额取不到：{type(exc).__name__}: {exc}")
            bal = {}

        try:
            positions = await adapter.get_positions()
            if positions:
                print(f"  持仓 {len(positions)} 个（浮动盈亏不产生流水）：")
                for p in positions:
                    print(
                        f"    {p.symbol} {p.side} qty={p.quantity} "
                        f"entry={p.entry_price} upnl={p.unrealized_pnl}"
                    )
            else:
                print("  持仓：无（空仓 → 没有浮动盈亏在影响可用余额）")
        except Exception as exc:  # noqa: BLE001
            print(f"  持仓取不到：{type(exc).__name__}: {exc}")

        # ── 2. 全历史流水 ──────────────────────────────────────────
        now = int(time.time() * 1000)
        start = now - DAYS * 86400 * 1000 if DAYS > 0 else DEFAULT_START_MS
        try:
            rows = await _fetch_all_income(adapter, start, now)
        except Exception as exc:  # noqa: BLE001
            print(
                f"\n  ⚠ 流水拉取失败：{type(exc).__name__}: {exc}\n"
                "    **这不是「没有流水」**，是这次没读到。别读成「什么都没发生」。"
            )
            return

        if not rows:
            print(
                f"\n  区间 {_ts(start)} → {_ts(now)} 内没有任何流水（请求成功，所以确实为空）"
            )
            return

        bytype: dict[str, Decimal] = defaultdict(Decimal)
        for r in rows:
            bytype[str(r.get("incomeType") or "UNKNOWN")] += _d(r.get("income"))

        span = f"{_ts(min(int(r['time']) for r in rows if r.get('time')))} → {_ts(max(int(r['time']) for r in rows if r.get('time')))}"
        print(f"\n  流水 {len(rows)} 条，{span}")
        print(f"  {'类型':<22} {'合计':>18}")
        for kind, amount in sorted(bytype.items(), key=lambda kv: kv[1]):
            print(f"  {kind:<22} {amount:>18.8f}")

        net = sum(bytype.values(), Decimal("0"))
        usdt = _d((bal or {}).get("USDT"))
        print(f"  {'-' * 42}")
        print(f"  {'流水合计':<22} {net:>18.8f}")
        if usdt:
            print(f"  {'当前 USDT 余额':<22} {usdt:>18.8f}")
            print(
                f"  {'差额':<22} {net - usdt:>18.8f}"
                f"   ← 为 0 说明每分钱都能解释，没有不明去向"
            )

        # ── 3. 逐单对账 ────────────────────────────────────────────
        per_order: dict[str, dict[str, Decimal]] = defaultdict(
            lambda: defaultdict(Decimal)
        )
        for r in rows:
            oid = str(r.get("orderId") or "")
            if oid:
                per_order[oid][str(r.get("incomeType") or "")] += _d(r.get("income"))

        local_rows = await db_async(
            lambda: list(
                Order.objects.filter(exchange_account_id=acc.id).values(
                    "exchange_order_id", "realized_pnl", "symbol", "side", "status"
                )
            )
        )()
        local = {
            o["exchange_order_id"]: o for o in local_rows if o["exchange_order_id"]
        }

        ex_only = [oid for oid in per_order if oid not in local]
        print(f"\n  交易所流水里的订单 {len(per_order)} 个 / 本地有行 {len(local)} 个")
        if ex_only:
            print(f"  ⚠ 交易所有、本地没有的订单 {len(ex_only)} 个（账本缺记录）：")
            mp = mf = Decimal("0")
            for oid in sorted(ex_only):
                d = per_order[oid]
                mp += d.get("REALIZED_PNL", Decimal("0"))
                mf += d.get("COMMISSION", Decimal("0"))
                print(
                    f"    {oid} pnl={d.get('REALIZED_PNL', 0):>12.6f} "
                    f"fee={d.get('COMMISSION', 0):>10.6f}"
                )
            print(f"    小计 未入账 pnl={mp:.8f} fee={mf:.8f}")
            print(
                "    回填：manage.py reconcile_realized_pnl_from_exchange（默认干跑）"
            )
        else:
            print("  ✓ 交易所有订单号的流水，本地都有对应 Order 行")

        diffs = []
        for oid, d in per_order.items():
            if oid not in local:
                continue
            ex = d.get("REALIZED_PNL", Decimal("0"))
            raw = local[oid]["realized_pnl"]
            lo = Decimal(str(raw)) if raw is not None else Decimal("0")
            if abs(ex - lo) >= Decimal("0.00000001"):
                diffs.append((oid, lo, ex, local[oid]))
        if diffs:
            print(f"\n  pnl 与交易所不一致 {len(diffs)} 笔：")
            total = Decimal("0")
            for oid, lo, ex, o in diffs:
                total += ex - lo
                print(
                    f"    {oid} 本地={lo:>12.6f} 交易所={ex:>12.6f} "
                    f"Δ={ex - lo:>12.6f} {o['symbol']} {o['side']}"
                )
            print(f"    合计 Δ={total:.8f}")
        else:
            print("  ✓ 已入账订单的 pnl 与交易所逐笔一致")

        # ── 结论 ──────────────────────────────────────────────────
        silent = bytype.get("COMMISSION", Decimal("0")) + bytype.get(
            "FUNDING_FEE", Decimal("0")
        )
        if silent:
            print("\n  ★ 本地账本从未记录的两类（按设计不产生 Order 行）：")
            print(f"      手续费 {bytype.get('COMMISSION', 0):>16.8f}")
            print(f"      资金费 {bytype.get('FUNDING_FEE', 0):>16.8f}")
            print(f"      合计   {silent:>16.8f} USDT  ← 这就是「余额变了却没有订单」")
    finally:
        try:
            await adapter.disconnect()
        except Exception:  # noqa: BLE001
            pass


async def main() -> None:
    qs = ExchangeAccount.objects.filter(is_active=True)
    if ONLY_ACCOUNT:
        qs = ExchangeAccount.objects.filter(id=ONLY_ACCOUNT)
    accounts = await db_async(lambda: list(qs.order_by("created_at")))()
    if not accounts:
        print("没有可审计的账户")
        return
    for acc in accounts:
        await audit(acc)


if __name__ == "__main__":
    asyncio.run(main())
