"""回答「余额变了，但系统里没有订单」——把账户资金流水与本地订单摆在一起对账。

## 它解决什么

用户看到的是一句无法反驳的观察：余额动了，交易记录里什么都没有。本地 ``orders`` 表
答不出这件事，**因为它按设计只记「本系统发出过一张单」**。而合约账户的 USDT 至少会被
四类流动改变，其中三类**根本不产生** ``Order`` 行：

| 流动 | 会落 ``Order`` 行吗 | 从哪知道 |
|---|---|---|
| 成交（平仓盈亏、吃单手续费） | 会（策略单 / 减仓单 / 回填行） | ``orders`` |
| **资金费**（FUNDING_FEE，每 8h 结算） | **不会** | 只有 ``income`` |
| **划转 / 充值 / 提现 / 强平清算** | **不会** | 只有 ``income`` |
| 未实现盈亏（浮盈浮亏） | 不是流水，但改「可用余额」 | ``positionRisk`` |

所以「余额变了却没有订单」在多数情况下不是账错了，而是**系统从来没有读过这两类流动**
——在本次改动之前，全仓没有一处调用 ``/fapi/v1/income``（``fetch_income`` 是第一个口）。
本命令就是那个口的第一个消费者：把交易所流水拉下来，与本地订单并排打印，并把「本地
一条订单都没有、交易所却有流水」的那些行**单独列成一节**。

## 它是只读的

**一行都不写库。** 它的产物是一份给人看的对照表，不是一个新真相源。要不要把资金费
也记进账本，是另一个决定（涉及「Order 表的语义」——那是「系统下的单」，不是「账户的
每一次余额变动」），不在本命令里替它作答。

## 为什么按账户遍历而不是按会话

资金费与划转是**账户级**的事实，与哪条会话在跑无关：同一个账户上跑两条会话时，只把
资金费摊给其中一条是编出来的归属。所以对照表按账户打，会话归因只在已有 ``Order`` 行
（它自带 ``live_session_id``）上显示。

## 时间窗

交易所的 ``income`` **不给起点只回最近 7 天**。本命令因此要求显式 ``--days``（默认 7），
并把实际查询区间打在表头上——「查了 7 天」与「查了全部」必须长得不一样，否则一次
「什么都没有」会被读成「从来没有过」。

## 干跑与真跑没有区别

因为它不写库。``--apply`` 是别的命令的约定，这里刻意不提供：一个只读命令不该有
「更危险的模式」，那只会让人以为默认那个是残的。
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand, CommandError

from apps.core.db_utils import db_async
from apps.exchange.models import ExchangeAccount
from apps.trading import reduce
from apps.trading.models import Order
from apps.trading.reduce_ledger import to_session_symbol

#: 交易所 ``income`` 的收益类型 → 中文说明。**只用于打印**：判定逻辑一律按原始
#: ``incomeType`` 字面值走（与 ``LiveSessionStatusChange`` 存字面值是同一条纪律——
#: 将来枚举增删，历史行必须保持它当时的原样）。
_INCOME_TYPE_LABELS = {
    "TRANSFER": "划转/充值/提现",
    "WELCOME_BONUS": "体验金",
    "REALIZED_PNL": "平仓盈亏",
    "FUNDING_FEE": "资金费",
    "COMMISSION": "手续费",
    "INSURANCE_CLEAR": "强平清算",
    "REFERRAL_KICKBACK": "推荐返佣",
    "COMMISSION_REBATE": "手续费返佣",
    "API_REBATE": "API 返佣",
    "CONTEST_REWARD": "活动奖励",
    "CROSS_COLLATERAL_TRANSFER": "联合保证金划转",
    "OPTIONS_PREMIUM_FEE": "期权权利金",
    "OPTIONS_SETTLE_PROFIT": "期权结算盈亏",
    "INTERNAL_TRANSFER": "内部划转",
    "AUTO_EXCHANGE": "自动兑换",
    "DELIVERED_SETTELMENT": "交割结算",
    "COIN_SWAP_DEPOSIT": "币币互换入金",
    "COIN_SWAP_WITHDRAW": "币币互换出金",
    "POSITION_LIMIT_INCREASE_FEE": "提高持仓上限费",
    "STRATEGY_UMFUTURES_TRANSFER": "策略账户划转",
    "FEE_RETURN": "手续费返还",
    "BFUSD_REWARD": "BFUSD 收益",
    "BFUSD_PERFORMANCE_FEE": "BFUSD 业绩费",
    "BFUSD_BONUS": "BFUSD 奖励",
}


def _decimal(value, default: Decimal | None = None) -> Decimal | None:
    """尽可能解析成 ``Decimal``；解析不了返回 ``default``（**不落 0**）。"""
    if value is None or isinstance(value, bool):
        return default
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return default


def _ts(ms) -> str:
    """毫秒时间戳 → ``YYYY-MM-DD HH:MM:SS``（**UTC**，与 Django ``TIME_ZONE`` 同口径）。

    这里的转换**必须**显式带 UTC 时区：不带的话拿到的是机器本地时区，于是表里每一行
    都偏移几小时——和资金费结算那次事故是同一个坑（CLAUDE.md 的 Timezone 段写的就是
    它，`scripts/check_invariants.py` 的 ``timezone_convention`` 也在守这一条）。
    """
    if ms is None:
        return "-"
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    except (TypeError, ValueError, OSError):
        return "-"


def _group_income_by_type(rows: list[dict]) -> dict[str, list[dict]]:
    """按 ``incomeType`` 分组。**纯函数**（便于单测，不碰 DB / 网络）。"""
    out: dict[str, list[dict]] = defaultdict(list)
    for row in rows or []:
        out[str(row.get("incomeType") or "UNKNOWN")].append(row)
    return dict(out)


def _sum_by_type(rows: list[dict]) -> dict[str, Decimal]:
    """每种收益类型的合计金额。解析不了的行计入 ``UNPARSED`` 计数而不是被悄悄丢掉。

    返回 ``{"_unparsed": Decimal(n)}`` 里那个键名带下划线，是为了与真实的
    ``incomeType`` 区分开——真实类型全是大写字母加下划线、不以 ``_`` 开头。
    """
    out: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for row in rows or []:
        amount = _decimal(row.get("income"))
        if amount is None:
            out["_unparsed"] += Decimal("1")
            continue
        out[str(row.get("incomeType") or "UNKNOWN")] += amount
    return dict(out)


def _income_without_local_order(
    rows: list[dict], local_order_ids: set[str]
) -> list[dict]:
    """挑出「交易所说发生了、本地却没有任何 ``Order`` 行」的流水。**纯函数**。

    这是本命令的核心判据。``Order`` 表按 ``(账户, 交易所单号)`` 挂单号，而资金费与划转
    **压根没有 ``orderId``**——所以它们必然落进这一节。而一笔 ``REALIZED_PNL`` 若出现在
    这里，说明本地确实缺行（2026-09-28 那类缺口，或路径从未落库）。

    ``orderId`` 只对 ``REALIZED_PNL`` / ``COMMISSION`` 这类与具体订单绑定的类型有意义；
    其余类型为空是**正常**的，不是缺口——所以「没有 orderId」本身不能判成缺失。
    """
    out: list[dict] = []
    for row in rows or []:
        order_id = str(row.get("orderId") or "")
        if order_id and order_id in local_order_ids:
            continue
        out.append(row)
    return out


class Command(BaseCommand):
    help = (
        "解释「余额变了但没有订单」：拉交易所资金流水与本地订单并排对照（只读，不写库）"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=7,
            help=(
                "回溯天数（默认 7）。交易所不给起点只回最近 7 天，"
                "所以更早的账必须显式放大这个值"
            ),
        )
        parser.add_argument(
            "--account",
            default=None,
            help="只处理这个交易所账户（ExchangeAccount UUID）。不给则全部活跃账户",
        )
        parser.add_argument(
            "--symbol",
            default=None,
            help="只看这个品种（斜杠口径，如 BTC/USDT）。不给则全品种",
        )
        parser.add_argument(
            "--type",
            default=None,
            help="只看这一种收益类型（如 FUNDING_FEE / TRANSFER）。不给则全部类型",
        )

    def handle(self, *args, **options):
        days = options["days"]
        if days <= 0:
            raise CommandError("--days 必须是正整数")
        asyncio.run(
            self._run(
                days=days,
                account=options["account"],
                symbol=options["symbol"],
                income_type=options["type"],
            )
        )

    # ─── 主流程 ────────────────────────────────────────────────────────────

    async def _run(
        self,
        *,
        days: int,
        account: str | None,
        symbol: str | None,
        income_type: str | None,
    ) -> None:
        accounts = await db_async(self._load_accounts)(account)
        if not accounts:
            self.stdout.write(self.style.WARNING("没有可处理的交易所账户"))
            return

        now_ms = int(time.time() * 1000)
        start_ms = now_ms - days * 24 * 3600 * 1000

        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"余额变动解释：查询区间 {_ts(start_ms)} → {_ts(now_ms)}（UTC，{days} 天）"
            )
        )
        self.stdout.write(
            "注意：交易所 /fapi/v1/income 不给起点时只回最近 7 天，"
            "所以表头打的是实际查询区间。"
        )

        for acc in accounts:
            await self._process_account(
                acc,
                start_ms=start_ms,
                end_ms=now_ms,
                symbol=symbol,
                income_type=income_type,
            )

    def _load_accounts(self, account: str | None):
        qs = ExchangeAccount.objects.filter(is_active=True)
        if account:
            qs = qs.filter(id=account)
        return list(qs.order_by("created_at"))

    async def _process_account(
        self,
        acc,
        *,
        start_ms: int,
        end_ms: int,
        symbol: str | None,
        income_type: str | None,
    ) -> None:
        from apps.trading.pending_reconcile import build_account_adapter

        self.stdout.write("")
        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"账户 {acc.id}（{acc.label or 'default'}，"
                f"{'模拟' if acc.testnet else '实盘'}）"
            )
        )

        try:
            adapter = await build_account_adapter(acc)
        except Exception as exc:  # noqa: BLE001
            self.stdout.write(self.style.WARNING(f"  适配器构建失败，跳过：{exc}"))
            return
        if adapter is None:
            self.stdout.write(
                self.style.WARNING("  无可用适配器（缺密钥/无该交易所），跳过")
            )
            return

        try:
            balances = await self._read_balances(adapter)
            rows = await self._fetch_income(
                adapter,
                start_ms=start_ms,
                end_ms=end_ms,
                symbol=symbol,
                income_type=income_type,
            )
        finally:
            try:
                await adapter.disconnect()
            except Exception:  # noqa: BLE001
                pass

        if rows is None:
            self.stdout.write(
                self.style.ERROR(
                    "  资金流水拉取失败——**这不是「没有流水」**，"
                    "而是这次没读到。别把它读成「什么都没发生」。"
                )
            )
            return

        self._print_balances(balances)
        self._print_income(rows)
        await self._print_gap(acc, rows, symbol=symbol)

    async def _read_balances(self, adapter) -> dict:
        """钱包 / 可用 / 未实现盈亏三块。取不到的那一块留 ``None``（不编 0）。"""
        out = {"wallet": None, "available": None, "unrealized_pnl": None, "total": None}
        try:
            detail = await adapter.get_balance_detail()
        except Exception:  # noqa: BLE001 — 细分口径是可选的（base 默认返回 {}）
            detail = {}
        row = detail.get("USDT") if isinstance(detail, dict) else None
        if isinstance(row, dict):
            for key in ("crossWalletBalance", "walletBalance", "balance"):
                if row.get(key) is not None:
                    out["wallet"] = row[key]
                    break
            if row.get("availableBalance") is not None:
                out["available"] = row["availableBalance"]
            if row.get("crossUnPnl") is not None:
                out["unrealized_pnl"] = row["crossUnPnl"]

        try:
            plain = await adapter.get_balance()
            out["total"] = _decimal((plain or {}).get("USDT"))
        except Exception:  # noqa: BLE001
            pass
        return out

    def _print_balances(self, balances: dict) -> None:
        def show(value):
            return "（未取到）" if value is None else f"{value:.8f}"

        self.stdout.write(
            f"  当前 USDT：总资产 {show(balances.get('total'))} | "
            f"钱包 {show(balances.get('wallet'))} | "
            f"可用 {show(balances.get('available'))} | "
            f"未实现盈亏 {show(balances.get('unrealized_pnl'))}"
        )
        self.stdout.write(
            "  「钱包」不含浮盈浮亏；「可用」会被持仓占用的保证金压低。"
            "两者差得越多，说明被占用的保证金越多。"
        )

    async def _fetch_income(
        self,
        adapter,
        *,
        start_ms: int,
        end_ms: int,
        symbol: str | None,
        income_type: str | None,
    ) -> list[dict] | None:
        native = None
        if symbol:
            native = reduce.normalize_symbol(to_session_symbol(symbol))
        try:
            return await adapter.fetch_income(
                symbol=native,
                income_type=income_type,
                start_ms=start_ms,
                end_ms=end_ms,
            )
        except NotImplementedError:
            self.stdout.write(
                self.style.WARNING(
                    "  该适配器不支持拉取资金流水（fetch_income 未实现）"
                )
            )
            return None
        except Exception as exc:  # noqa: BLE001 — 「取不到」必须与「没有」分开
            self.stdout.write(
                self.style.WARNING(f"  拉取失败：{type(exc).__name__}: {exc}")
            )
            return None

    def _print_income(self, rows: list[dict]) -> None:
        if not rows:
            self.stdout.write(
                self.style.WARNING(
                    "  该区间内交易所没有返回任何流水。"
                    "（空 ≠ 没查；本次请求是成功的，所以这里确实是空的）"
                )
            )
            return

        totals = _sum_by_type(rows)
        self.stdout.write(f"  交易所流水 {len(rows)} 条，按类型合计：")
        order = sorted(
            ((k, v) for k, v in totals.items() if k != "_unparsed"),
            key=lambda kv: (-abs(kv[1]), kv[0]),
        )
        for kind, amount in order:
            label = _INCOME_TYPE_LABELS.get(kind, kind)
            self.stdout.write(f"    {kind:<26} {label:<14} {amount:>18.8f}")
        if totals.get("_unparsed"):
            self.stdout.write(
                self.style.WARNING(
                    f"    （另有 {int(totals['_unparsed'])} 条流水金额解析不出来，"
                    "已计入条数、未计入合计）"
                )
            )

        net = sum((v for k, v in totals.items() if k != "_unparsed"), Decimal("0"))
        self.stdout.write(f"    {'合计':<26} {'':<14} {net:>18.8f}")
        self.stdout.write("")

        # 明细：只打「不是成交」的那几类 + 全部（条数少时才全打）。成交类动辄上百条，
        # 而用户要看的是「除了成交，还有谁动了我的钱」。
        non_trade = [
            r
            for r in rows
            if str(r.get("incomeType") or "") not in ("REALIZED_PNL", "COMMISSION")
        ]
        if non_trade:
            self.stdout.write("  非成交类流水（这些**从来不产生订单**）：")
            self.stdout.write(
                f"    {'时间(UTC)':<20} {'类型':<22} {'品种':<12} {'金额':>16} {'订单号':<14}"
            )
            for row in sorted(non_trade, key=lambda r: int(r.get("time") or 0)):
                kind = str(row.get("incomeType") or "")
                amount = _decimal(row.get("income"), Decimal("0"))
                self.stdout.write(
                    f"    {_ts(row.get('time')):<20} "
                    f"{_INCOME_TYPE_LABELS.get(kind, kind):<22} "
                    f"{str(row.get('symbol') or '-'):<12} "
                    f"{amount:>16.8f} "
                    f"{str(row.get('orderId') or '-'):<14}"
                )
        else:
            self.stdout.write("  该区间内的流水全部是成交类（平仓盈亏/手续费）。")

    async def _print_gap(self, acc, rows: list[dict], *, symbol: str | None) -> None:
        """把「交易所有流水、本地没有订单」的那些行单独列出来——本命令的落点。"""
        if not rows:
            return

        order_ids = {str(r.get("orderId")) for r in rows if r.get("orderId")}

        @db_async
        def _local_ids():
            qs = Order.objects.filter(exchange_account_id=acc.id)
            if order_ids:
                qs = qs.filter(exchange_order_id__in=list(order_ids))
            return set(qs.values_list("exchange_order_id", flat=True))

        try:
            local = await _local_ids()
        except Exception as exc:  # noqa: BLE001
            self.stdout.write(
                self.style.WARNING(f"  本地订单查询失败，跳过缺口对照：{exc}")
            )
            return

        missing = _income_without_local_order(rows, local)
        # 「与订单无关」的那些（资金费、划转）不是缺口，是**本来就不该有行**的流动；
        # 真正值得点名的是「带了 orderId、本地却没有这一行」——那是账本缺了一条记录。
        true_gaps = [r for r in missing if r.get("orderId")]

        self.stdout.write("")
        if true_gaps:
            self.stdout.write(
                self.style.ERROR(
                    f"  ⚠ 有 {len(true_gaps)} 条成交流水在本地 orders 表里找不到对应行："
                    "（账本缺记录，需要回填）"
                )
            )
            for row in sorted(true_gaps, key=lambda r: int(r.get("time") or 0)):
                self.stdout.write(
                    f"    订单号 {row.get('orderId')} "
                    f"{_ts(row.get('time'))} "
                    f"{str(row.get('symbol') or '-')} "
                    f"{row.get('incomeType')} "
                    f"{_decimal(row.get('income'), Decimal('0')):.8f}"
                )
            self.stdout.write(
                "    回填入口：manage.py reconcile_realized_pnl_from_exchange "
                "（默认干跑，确认后加 --apply）"
            )
        else:
            bound = [r for r in rows if r.get("orderId")]
            if bound:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"  ✓ 带订单号的 {len(bound)} 条流水，本地都有对应的 Order 行"
                    )
                )

        unrelated = len(missing) - len(true_gaps)
        if unrelated:
            self.stdout.write(
                f"  {unrelated} 条流水与订单无关（资金费 / 划转 / 返佣等），"
                "它们改变余额但**按设计不产生 Order 行**。"
            )
            self.stdout.write(
                "  → 「余额变了却没有订单」如果就是这个量级，那账没丢，"
                "只是这类流动此前没有任何读面。"
            )
