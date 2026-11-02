#!/usr/bin/env python
"""升实盘前置体检（CLI）：把 `apps.trading.preflight` 的结论打成人读的清单。

用法（cwd = backend/）：

    # 单条会话（给目标账户就跑完整的四道账户闸门）
    python scripts/live_promotion_preflight.py --paper-session <PAPER_ID> \\
        --live-account <LIVE_ACCOUNT_ID>

    # 只给会话：账户那四道落 skip，其余照跑
    python scripts/live_promotion_preflight.py --paper-session <PAPER_ID>

    # 扫一遍全部候选（含还没走到 paper 的那些）
    python scripts/live_promotion_preflight.py --scan

    # 机器可读（给 CI / 别的脚本消费）；退出码口径见下
    python scripts/live_promotion_preflight.py --paper-session <ID> --json

## 退出码

    0  没有 fail（可能带 warn / skip）
    1  有 fail —— 按下 promote 会被拒，或会话起来后第一根 bar 就被风控打死
    2  参数 / 环境错误（会话不存在、不是 paper 模式、Django 起不来）

**`skip` 不算 1**：它只说明这一条没问到（Redis 不可达、没给目标账户、策略类解析不到）。
把它算成失败，人会去修一件体检没问到的事，而真正的 fail 被埋在后面。要「一个 skip 都
不许有」，加 `--strict`——那是给上线检查用的另一个口径，不是默认口径。

## 这个脚本自己不做判断

判据全在 `apps/trading/preflight.py`（它又全是去问各处唯一出处）。这里只做三件事：
解析参数、调它、把 `Check` 渲染成一行一行的中文。**任何一条判据都不许写在这个文件里**
——脚本没有测试，判据写在脚本里就等于写在测试之外。

## 为什么要把 fd 1 临时挪到 stderr

`StrategyRegistry.discover()` 会 **import** `~/.tradelogx/strategies/` 下每一个 `.py`，
而那里躺着一个 `list_strategies.py`，它在**模块级** print 十几段策略说明。于是任何一次
`--scan`（它要读生效参数 → 解析策略类 → 触发 discover）都会把这些行混进 stdout，`--json`
直接变成不可解析的文本——实测在 txy 上就是这样。

所以整个「跑体检」过程把 fd 1 重定向到 fd 2：那些噪音落到 stderr（该去的地方），
真结果等 `finally` 把 fd 1 换回来之后再打。用 `os.dup2` 而不是
`contextlib.redirect_stdout`：后者只改 Python 层的 `sys.stdout`，而这里要挡的写入不一定
全走 Python 那一层。
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys

_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings.dev")


@contextlib.contextmanager
def _stdout_as_stderr():
    """把 fd 1 临时接到 fd 2 上；退出时原样接回去。

    `sys.stdout` 在 `dup2` 之后仍然指向 fd 1，只是 fd 1 此刻就是 stderr 那一端——所以
    Python 层的 `print` **以及** C 层 / 子进程写 fd 1 的输出都会被挪走。这正是要的效果：
    策略注册表 import 期间的一切噪音都不许进结果流。
    """
    try:
        sys.stdout.flush()
        saved = os.dup(1)
    except (OSError, ValueError):
        # 没有可用的 fd 1（被调用方接成了对象而不是真实 fd）：退化成只改 Python 层。
        with contextlib.redirect_stdout(sys.stderr):
            yield
        return
    try:
        os.dup2(2, 1)
        with contextlib.redirect_stdout(sys.stderr):
            yield
    finally:
        sys.stdout.flush()
        os.dup2(saved, 1)
        os.close(saved)


#: 每档的标记。**不用颜色**：这份输出经常被贴进工单与聊天，ANSI 码会变成乱码。
_MARK = {
    "pass": "✓",
    "warn": "!",
    "fail": "✗",
    "skip": "·",
}

_HEADLINE = {
    "go": "可以升",
    "go_with_warnings": "可以升（但有需要你知道的条目）",
    "no_go": "不能升",
    # 第四条结论：这条候选还没有 paper 会话，「升实盘」在它身上还不成立。
    # 与 `go_with_warnings` 分开说——合成一句会让 `--scan` 读出「这两条可以升」。
    "not_applicable": "还没到 paper 层，没有可升的对象",
}


def _render(report, *, width: int = 0) -> list[str]:
    lines: list[str] = []
    ident = report.paper_session_id or "（无 paper 会话）"
    ident = f"{ident[:8]}…" if len(ident) > 12 else ident
    head = f"paper 会话 {ident}"
    if report.candidate_id:
        head += f" / 候选 {report.candidate_id[:8]}…"
    lines.append(head)

    for check in report.checks:
        mark = _MARK.get(check.status, "?")
        line = f"  {mark} [{check.status:4}] {check.title}"
        if check.detail:
            line += f" — {check.detail}"
        if width:
            line = _wrap(line, width)
        lines.append(line)

    lines.append(f"  结论：{_HEADLINE.get(report.verdict, report.verdict)}")
    if report.blocking:
        lines.append("  必须先解决：" + "、".join(c.key for c in report.blocking))
    return lines


def _wrap(line: str, width: int) -> str:
    """按宽度折行，续行缩进对齐。只折 detail，不折标题。

    `textwrap` 不直接用：它对中文断行无所谓（没有空格），却会把 `—` 那一小段也当成
    可折的普通文本。这里只要「别把一行撑到 400 字符」，所以手工按字符数切。
    """
    if len(line) <= width:
        return line
    indent = " " * (len(line) - len(line.lstrip()) + 4)
    out: list[str] = []
    rest = line
    while len(rest) > width:
        out.append(rest[:width])
        rest = indent + rest[width:]
    out.append(rest)
    return "\n".join(out)


def _exit_code(reports, *, strict: bool) -> int:
    """0 = 没有 fail；1 = 有 fail（`--strict` 时 skip 也算）；2 由调用方给。

    `applicable=False` 的报告（还没走到 paper 层）**不参与**这个判断：它没有可判的对象。
    照旧让它过 `strict` 那一支会把「还没到 L3」判成「上线检查不通过」，而两条的处置相反
    （前者什么都不用做，后者要改参数重跑）。
    """
    for report in reports:
        if report.blocking:
            return 1
        if strict and report.applicable and report.skips:
            return 1
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="升实盘前置体检（只读）：promote 会撞上的每一道闸门先问一遍",
    )
    parser.add_argument("--paper-session", help="paper 会话 id（UUID）")
    parser.add_argument(
        "--live-account",
        help="目标实盘账户 id（UUID）。不给则账户那四道闸门落 skip",
    )
    parser.add_argument(
        "--scan",
        action="store_true",
        help="扫一遍全部候选（含还没走到 paper 的那些）",
    )
    parser.add_argument("--json", action="store_true", help="机器可读输出")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="skip 也算不通过（上线检查口径；默认口径下 skip 不改结论方向）",
    )
    args = parser.parse_args(argv)
    if not args.paper_session and not args.scan:
        parser.error("要么给 --paper-session，要么给 --scan")
    return args


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    # Django 起来之后才会触碰策略注册表（`--scan` 读生效参数时会 discover），
    # 所以整个 setup + 体检都在这把「fd 1 → stderr」的伞下面。
    with _stdout_as_stderr():
        import django

        django.setup()

        from apps.trading import preflight

        reports = []
        error: str | None = None
        if args.scan:
            reports = preflight.scan()
        else:
            session = preflight.find_paper_session(args.paper_session)
            if session is None:
                error = (
                    f"❌ 找不到 mode=paper 的会话 {args.paper_session!r}"
                    "（不存在，或者它是一条 live 会话）"
                )
            else:
                account = None
                if args.live_account:
                    account = preflight.find_live_account(args.live_account)
                    if account is None:
                        error = f"❌ 找不到交易所账户 {args.live_account!r}"
                if error is None:
                    reports = [
                        preflight.preflight(paper_session=session, live_account=account)
                    ]

    if error is not None:
        print(error, file=sys.stderr)
        return 2

    if not reports:
        print("没有任何候选。", file=sys.stderr)
        return 0

    if args.json:
        print(
            json.dumps(
                [r.as_payload() for r in reports]
                if args.scan
                else reports[0].as_payload(),
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        width = 0 if sys.stdout.isatty() else 100
        for index, report in enumerate(reports):
            if index:
                print()
            print("\n".join(_render(report, width=width)))

    return _exit_code(reports, strict=args.strict)


if __name__ == "__main__":
    sys.exit(main())
