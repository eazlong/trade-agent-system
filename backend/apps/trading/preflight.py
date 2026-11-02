"""升实盘前置体检（**只读**）：把 promote 会撞上的每一道闸门先问一遍。

## 这个模块只回答一件事

「这条 paper 会话现在点『升级为实盘』会发生什么」。它**不建会话、不改状态、不写流水、
不下单**——一行 DB 写入都没有。理由：真去点 promote 的代价不低（一次 400 之后才知道少
了个账户、或者升级完了才发现 paper runner 还在往 stream 投单），而这道闸门的判据散在
四处——`apps.strategy_factory.live_gate`、`LiveSession` 自己的两个 model 方法、
`apps.regime.halt`、`RiskGuard` 的类常量——一次问清楚比点五次便宜。

## 三条纪律（与 `live_gate` 同源，不新造口径）

1. **判定口径不复制。** 「跑满观察期且判定通过」只问 `live_gate.evaluate_live_gate`；
   同账户同品种冲突只问 `LiveSession.conflicting_active_session`；此刻在拦的层只问
   `halt.halt_layers`；paper 那一层的证据只问 `effects.read_paper`（它才是 D7/D8 的
   唯一采集处）。本模块把它们的**结论摊开成逐条**，不是重新实现一遍。凡本模块自己算的
   数（单仓占比的近似比较），都必须标明是近似。

2. **只读。** 不 `save()` / 不 `create()` / 不 `delete()`。`read_paper` 会把 caveats 暂存
   在一个模块级字典里（编排层的既有形状），所以这里采完必须用 `take_paper_caveats` 取走，
   否则一次体检就在进程里留一份垃圾。

3. **降级不等于通过。** 读不到就 `skip` 并写清为什么（Redis 不可达、`celery_app` 导不进、
   策略类解析不到、没给目标账户）。`skip` 既不计入「可以升」也不计入「不行」——它只说明
   「这一条我没问到」。`fail` 只有两个来源：**promote 那一刻会被 400 拒掉**，或
   **会话起来之后第一根 bar 就被风控打死**。其余一律 `warn`。

## 为什么要有 `warn` 这一档

`halt_layers` 命中不阻止升级（升级建的是会话，不是订单），但升级之后那条会话开不了新仓；
`REGIME_GATE` 在 `Shadow` 档下同样不阻止升级，但机制的三条自动动作线一条都不生效。
这两件事都有一个共同形状：**升级会成功，而你以为你买到的东西不在**。那是 `warn` 的用途，
不是 `fail` 的——把它们判成 fail 会诱导人去改一件本来没错的事。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

PASS = "pass"
WARN = "warn"
FAIL = "fail"
SKIP = "skip"

#: 「贴着上限」的判定比例。**不是风控阈值**，只是体检的一句提醒：占比到这个刻度时，
#: 账户余额比总资金少两成就会开始被拒。风控真正比的那个数在
#: `RiskGuard.MAX_POSITION_RATIO`，这里不复制、不覆盖它。
_TIGHT_RATIO = Decimal("0.8")

#: 一条会话能升实盘的状态集合——**照抄 `trading.views.live_session_promote` 的那一行**
#: （`("running", "stopped", "paused")`）。这里不 import 视图（那会把 DRF 拖进来），
#: 所以它是一份副本；`test_live_promotion_preflight` 里有一条用例钉着它与视图一致。
PROMOTABLE_SESSION_STATUSES = ("running", "stopped", "paused")

#: 与「升级之后跑得起来」直接相关的 beat 条目。**不是全部条目**：体检只关心这四条，
#: 其余条目的死活不影响这次升级（`check-signals` 挂了是另一件事）。
CRITICAL_BEAT_NAMES = (
    "snapshot-daily-equity",
    "strategy-factory-advance",
    "regime-gate-sync",
    "check-order-status",
)

#: `frame:live_session:<id>` 这个 Redis 兼容层键的库号（`settings.REDIS_DB_FRAME`）。
#: 写在这里只是为了在 `settings` 取不到时有个兜底，不是第二份配置。
_FRAME_REDIS_DB_FALLBACK = 8


@dataclass(frozen=True)
class Check:
    """体检的一条结论。`key` 稳定（JSON 消费方与用例都按它断言），`title` 给人读。"""

    key: str
    title: str
    status: str
    detail: str = ""

    @property
    def blocking(self) -> bool:
        return self.status == FAIL


@dataclass(frozen=True)
class Report:
    """一次体检的全部结论。

    `applicable` 是**第四种结论**，与三档 verdict 并列而不是折进 `skip` 里：一条还没走到
    L3 的候选（`in_sample_failed` / `oos_running`）压根没有「升实盘」这件事可体检。把它
    报成 `go_with_warnings`，`--scan` 的输出会读出「这两条可以升」——而它们连 paper 会话
    都还没有。折进 `skip` 也不行：`--strict` 会把「合理的缺席」判成不通过。

    所以 `applicable=False` 的报告：`verdict` 是 `not_applicable`，退出码不计，且**不出任何
    fail**（没有可判的对象）。它的用途只有一个——让 `--scan` 说清「这条候选现在在哪一层」。
    """

    paper_session_id: str | None
    candidate_id: str | None
    checks: tuple[Check, ...] = field(default_factory=tuple)
    applicable: bool = True

    @property
    def blocking(self) -> tuple[Check, ...]:
        return tuple(c for c in self.checks if c.status == FAIL)

    @property
    def warnings(self) -> tuple[Check, ...]:
        return tuple(c for c in self.checks if c.status == WARN)

    @property
    def skips(self) -> tuple[Check, ...]:
        return tuple(c for c in self.checks if c.status == SKIP)

    @property
    def verdict(self) -> str:
        """`no_go` / `go_with_warnings` / `go` / `not_applicable`。

        **`no_go` 只由 `fail` 决定**：`skip` 与 `warn` 都不改这个结论的方向，它们只决定
        「这句话要不要打折扣地读」。把 skip 算成 no_go，人就会去修一件体检工具没问到的
        事，而真正的 fail 被埋在后面。
        """
        if not self.applicable:
            return "not_applicable"
        if self.blocking:
            return "no_go"
        if self.warnings or self.skips:
            return "go_with_warnings"
        return "go"

    def get(self, key: str) -> Check | None:
        for check in self.checks:
            if check.key == key:
                return check
        return None

    def as_payload(self) -> dict[str, Any]:
        return {
            "paper_session_id": self.paper_session_id,
            "candidate_id": self.candidate_id,
            "applicable": self.applicable,
            "verdict": self.verdict,
            "blocking": [c.key for c in self.blocking],
            "warnings": [c.key for c in self.warnings],
            "skips": [c.key for c in self.skips],
            "checks": [
                {
                    "key": c.key,
                    "title": c.title,
                    "status": c.status,
                    "detail": c.detail,
                }
                for c in self.checks
            ],
        }


# --------------------------------------------------------------------------- #
# 小件
# --------------------------------------------------------------------------- #


def _check(key: str, title: str, status: str, detail: str = "") -> Check:
    return Check(key=key, title=title, status=status, detail=detail)


def _fmt_days(value: float | None) -> str:
    return "算不出来" if value is None else f"{value:.2f} 天"


def _beat_entries() -> tuple[list[Any], list[str]]:
    """调度表条目与文件声明的条目名。导不进就抛，由调用方落 `skip`。

    延迟 import：`celery_app` 在 import 时会拉起任务模块与策略目录，而体检脚本不该把那份
    重量带进每一次调用（与 `apps/regime/beat_health_run.py` 同一条理由）。
    """
    from apps.regime import beat_health_run

    return beat_health_run.entries(), beat_health_run.declared()


def _frame_runner_alive(paper_session_id: str) -> bool | None:
    """Redis 兼容层键里这条会话还写着 `running` 吗；读不到返回 `None`。

    **为什么这条值得单开一项**：`live_session_promote` 只把 DB 状态写成 `stopped`，函数体
    里没有任何一处 `stop_strategy_runner`；而 `LiveStrategyRunner` 全文不重读会话状态。
    于是升级之后 paper runner 继续跑、继续往 `trading:orders` 投单（订单挂在一条已 stopped
    的会话上），直到进程重启。这一项就是那个窗口的探针。

    `None`（读不到 Redis）与 `False`（读到了、不在跑）必须分开：合成一个布尔之后，
    「Redis 连不上」会被读成「runner 已经停了」——那正是这条检查要防的方向。
    """
    try:
        import redis
        from django.conf import settings

        url = settings.REDIS_URL
        db = getattr(settings, "REDIS_DB_FRAME", _FRAME_REDIS_DB_FALLBACK)
        client = redis.Redis.from_url(
            url, db=db, decode_responses=True, socket_timeout=2
        )
        status = client.hget(f"frame:live_session:{paper_session_id}", "status")
    except Exception:  # noqa: BLE001 - 读不到就是读不到，由调用方落 skip
        return None
    return status == "running"


# --------------------------------------------------------------------------- #
# 各段检查
# --------------------------------------------------------------------------- #


def _session_checks(session: Any) -> list[Check]:
    if session.mode == "paper":
        checks = [_check("session.mode", "来源会话是模拟盘会话", PASS, session.symbol)]
    else:
        # 传进来的是一条 live 会话：`promote` 的第一句就是 400（"Only paper sessions"）。
        checks = [
            _check(
                "session.mode",
                "来源会话是模拟盘会话",
                FAIL,
                f"mode={session.mode}：promote 只接受 paper 会话",
            )
        ]

    if session.status in PROMOTABLE_SESSION_STATUSES:
        checks.append(
            _check(
                "session.status",
                "会话状态允许升实盘",
                PASS,
                f"status={session.status}（允许 {PROMOTABLE_SESSION_STATUSES}）",
            )
        )
    else:
        checks.append(
            _check(
                "session.status",
                "会话状态允许升实盘",
                FAIL,
                f"status={session.status}，promote 会直接 400；"
                f"只接受 {PROMOTABLE_SESSION_STATUSES} 之一",
            )
        )

    # 不变式：paper ⇒ testnet=True。错配意味着「以为在演练、实际在动钱」的反面
    # （真在演练、账面按实盘读），而 `mode_account_mismatch` 会在建新会话时拒绝它。
    mismatch = session.mode_account_mismatch()
    if mismatch:
        checks.append(
            _check("session.account_invariant", "会话与账户属性自洽", FAIL, mismatch)
        )
    else:
        checks.append(
            _check(
                "session.account_invariant",
                "会话与账户属性自洽",
                PASS,
                "paper 绑的是 testnet 账户",
            )
        )
    return checks


def _candidate_checks(session: Any) -> tuple[list[Check], Any | None]:
    """候选主行与四层判定的落点。返回 `(checks, candidate或None)`。

    「跑满观察期且判定通过」这一条**只问 `evaluate_live_gate`**——它是该口径的唯一出处
    （`live_gate` 的 docstring 写死了「不在这里重算观察期」）。本函数另外查一次候选行，
    只为把「查不到主行」与「状态不对」分成两条可读的结论，不构成第二个判据。
    """
    from apps.strategy_factory.live_gate import APPROVED_STATUS, evaluate_live_gate
    from apps.strategy_factory.models import StrategyCandidate

    rows = list(
        StrategyCandidate.objects.filter(paper_session=session).order_by("created_at")
    )
    if not rows:
        return (
            [
                _check(
                    "candidate.row",
                    "存在候选主行",
                    FAIL,
                    "这条 paper 会话没有对应的 StrategyCandidate.paper_session。"
                    "升实盘的前置是无从确认的——请走候选流水线（登记 → 样本内 → "
                    "样本外 → paper 观察），或按 runbook 补一条带证据的候选行",
                )
            ],
            None,
        )

    candidate = rows[0]
    checks = [
        _check(
            "candidate.row",
            "存在候选主行",
            PASS,
            f"{candidate.name}（{candidate.pk}）",
        )
    ]
    if len(rows) > 1:
        checks.append(
            _check(
                "candidate.unique",
                "一条会话只挂一条候选",
                WARN,
                f"有 {len(rows)} 条候选指向这条会话；闸门取最早的一条，"
                "其余不会参与判定",
            )
        )

    gate = evaluate_live_gate(session)
    if gate.allowed:
        checks.append(
            _check(
                "candidate.status",
                "候选已跑满观察期且判定通过",
                PASS,
                f"status={APPROVED_STATUS}",
            )
        )
    else:
        checks.append(
            _check("candidate.status", "候选已跑满观察期且判定通过", FAIL, gate.reason)
        )
    return checks, candidate


def _paper_checks(candidate: Any, *, now: datetime) -> list[Check]:
    """L3 的证据：观察期两个数、时间线在不在、三项统计过没过的**读数**。

    判定结论不在这里下（那是 `candidate.status` 那一条的事）。这里给的是**数字**：
    状态是 `paper_running` 时要回答的第一个问题就是「差多少」，而只报一个原因码答不了它。
    """
    from apps.strategy_factory.config import CONFIG
    from apps.strategy_factory.effects import read_paper, take_paper_caveats
    from apps.trading.models import running_duration

    session = candidate.paper_session
    evidence = read_paper(candidate, now=now)
    # `read_paper` 把 caveats 暂存在模块级字典里（编排层的一次性传手）。体检也走它，
    # 就必须负责收掉，否则一次体检在进程里留一份垃圾。
    caveats = take_paper_caveats(candidate)

    if evidence is None:
        return [
            _check(
                "paper.evidence",
                "paper 层证据可采",
                SKIP,
                "候选没有 paper 会话（或会话行不见了），观察期无从采起",
            )
        ]

    checks: list[Check] = []

    # 时间线：`running_duration` 在无时间线时返回 None，**不是 0**。
    # 这一项必须独立成条：判定的后果是 `insufficient_evidence`（一直等），看起来与
    # 「观察期没满」一模一样，而两者的处置相反（前者要补时间线，后者只要继续跑）。
    raw_running = running_duration(session, until=now)
    if raw_running is None:
        checks.append(
            _check(
                "paper.timeline",
                "会话状态时间线存在",
                FAIL,
                "LiveSessionStatusChange 一条都没有：running 时长算不出来，"
                "判定会落「证据不足」并一直停在 paper_running。"
                "该表是迁移 0008 才有的，之前建的会话需要先回填时间线",
            )
        )
    else:
        checks.append(
            _check(
                "paper.timeline",
                "会话状态时间线存在",
                PASS,
                f"running 累计 {_fmt_days(evidence.running_days)}",
            )
        )

    required = CONFIG.paper_observation_days
    wall_ok = (
        evidence.wall_clock_days is not None and evidence.wall_clock_days >= required
    )
    run_ok = evidence.running_days is not None and evidence.running_days >= required
    detail = (
        f"墙钟 {_fmt_days(evidence.wall_clock_days)}、running "
        f"{_fmt_days(evidence.running_days)}，要求两者都 ≥ {required} 天"
    )
    if wall_ok and run_ok:
        checks.append(_check("paper.observation", "观察期跑满", PASS, detail))
    else:
        # 观察期没满**不是** fail：它只是「还没到时候」。fail 留给「按下 promote 会被拒」，
        # 而这条会在 `candidate.status` 上体现为那条闸门的原话。
        checks.append(
            _check(
                "paper.observation",
                "观察期跑满",
                WARN,
                detail + "（paused 不计入 running）",
            )
        )

    trades_ok = (
        evidence.closed_trades is not None
        and evidence.closed_trades >= CONFIG.min_trades
    )
    return_ok = (
        evidence.return_pct is not None
        and evidence.return_pct >= CONFIG.paper_min_return_pct
    )
    hits_ok = (
        evidence.risk_hits is not None
        and evidence.risk_hits <= CONFIG.paper_max_risk_hits
    )
    stat_detail = (
        f"成交 {evidence.closed_trades} 笔（要求 ≥ {CONFIG.min_trades}）、"
        f"盈亏 {evidence.return_pct if evidence.return_pct is None else round(evidence.return_pct, 4)}%"
        f"（要求 ≥ {CONFIG.paper_min_return_pct}%）、"
        f"风控命中 {evidence.risk_hits} 次（要求 ≤ {CONFIG.paper_max_risk_hits}）"
    )
    checks.append(
        _check(
            "paper.stats",
            "paper 期三项统计达标",
            PASS if (trades_ok and return_ok and hits_ok) else WARN,
            stat_detail + ("；口径：" + "；".join(caveats) if caveats else ""),
        )
    )

    if evidence.session_ended:
        checks.append(
            _check(
                "paper.session_ended",
                "会话仍在观察期内",
                WARN,
                "会话已 stopped/error：观察期没跑满就结束的话，判定会落 paper_failed",
            )
        )
    return checks


def _regime_checks(candidate: Any | None) -> list[Check]:
    """行情闸门与机制档位。**两条都要**，因为它们回答的是不同问题。

    * 档位回答「机制现在会不会拦」（`Shadow` 下判了也不生效）；
    * 判定回答「按现在这片行情，这一格怎么说」。

    只看档位会把「判不了」读成「没风险」；只看判定会把一次 `Shadow` 期的放行读成
    「行情适用」。两条并列印出来，读的人自己就能对上。
    """
    from apps.regime import halt
    from apps.regime.models import MechanismKind

    checks: list[Check] = []
    mode = halt.switch_mode(MechanismKind.REGIME_GATE)
    enforcing = halt.is_open_mode(mode)
    checks.append(
        _check(
            "regime.mode",
            "机制档位（REGIME_GATE）",
            PASS if enforcing else WARN,
            f"当前 {mode.display}。"
            + (
                "三条自动动作线全部生效"
                if enforcing
                else "判据照跑、依据照落，但停用决策 / 暂停开新仓 / 候选行情闸门都不施加动作"
                "（要拦就 /regime gate on）"
            ),
        )
    )

    if candidate is None:
        checks.append(
            _check(
                "regime.verdict", "当前行情适用性", SKIP, "没有候选主行，无从问这一格"
            )
        )
        return checks

    from apps.strategy_factory.effects import read_regime
    from apps.strategy_factory.evaluate import regime_verdict
    from apps.strategy_factory.status import StrategyCandidateStatus

    evidence = read_regime(candidate)
    if evidence is None:
        checks.append(
            _check(
                "regime.verdict",
                "当前行情适用性",
                WARN,
                "拿不到适用性结论（冷启动：没有当代池化表，或当前没有生效阶段）。"
                "闸门对这种情况 fail-closed——候选会停在 regime_hold",
            )
        )
        return checks

    verdict = regime_verdict(
        candidate.strategy_id,
        evidence.regime,
        evidence.cell,
        evidence.exemptions,
        StrategyCandidateStatus.AWAITING_LIVE_APPROVAL,
    )
    cell_state = None if evidence.cell is None else evidence.cell.state
    if verdict is None:
        checks.append(
            _check(
                "regime.verdict",
                "当前行情适用性",
                PASS,
                f"阶段 {evidence.regime}、格子 {cell_state}：闸门不发表意见（保命档或适用）",
            )
        )
    else:
        checks.append(
            _check(
                "regime.verdict",
                "当前行情适用性",
                WARN,
                f"阶段 {evidence.regime}、格子 {cell_state}："
                f"{verdict.reason_code.display}"
                + ("" if enforcing else "（档位不是执行态，这一条此刻不生效）"),
            )
        )
    return checks


def _live_account_checks(session: Any, live_account: Any | None) -> list[Check]:
    """目标实盘账户。**四道**，与 `live_session_promote` 的四道一一对应。"""
    if live_account is None:
        return [
            _check(
                "live_account.specified",
                "已指定目标实盘账户",
                SKIP,
                "没给 --live-account：账户相关的四道闸门（存在/启用、testnet=False、"
                "同账户同品种冲突）没有问。补齐参数再跑一次才算完整体检",
            )
        ]

    checks: list[Check] = []
    if live_account.is_active:
        checks.append(
            _check(
                "live_account.active",
                "目标账户存在且启用",
                PASS,
                f"{live_account.label or live_account.exchange}（{live_account.pk}）",
            )
        )
    else:
        checks.append(
            _check(
                "live_account.active",
                "目标账户存在且启用",
                FAIL,
                f"账户「{live_account.label}」is_active=False",
            )
        )

    if live_account.testnet:
        checks.append(
            _check(
                "live_account.testnet",
                "目标账户是实盘账户（testnet=False）",
                FAIL,
                f"账户「{live_account.label}」testnet=True："
                "paper 账户不能当实盘账户用，promote 会 400",
            )
        )
    else:
        checks.append(
            _check(
                "live_account.testnet",
                "目标账户是实盘账户（testnet=False）",
                PASS,
                "testnet=False",
            )
        )

    conflict = session.conflicting_active_session(live_account, session.symbol)
    if conflict:
        checks.append(
            _check("live_account.conflict", "同账户同品种无活跃会话", FAIL, conflict)
        )
    else:
        checks.append(
            _check(
                "live_account.conflict",
                "同账户同品种无活跃会话",
                PASS,
                f"{session.symbol} 上只有这一条",
            )
        )
    return checks


def _halt_checks(session: Any, *, now: datetime) -> list[Check]:
    """此刻在拦的层。**只 warn**：升级建的是会话，不是订单。"""
    from apps.regime import halt

    verdict = halt.halt_layers(
        session.symbol,
        str(session.strategy_id) if session.strategy_id else None,
        now=now,
    )
    if not verdict.layers:
        return [_check("halt.layers", "会话此刻不受停止判定拦截", PASS, "没有命中的层")]
    return [
        _check(
            "halt.layers",
            "会话此刻不受停止判定拦截",
            WARN,
            f"{len(verdict.layers)} 层在拦：{verdict.reason}。"
            "这不阻止升级，但升级后那条会话开不了新仓（存量仓位的止损/平仓照常放行）",
        )
    ]


def risk_cap_check(
    symbol: str, position_pct: float | None, *, perp: bool = False
) -> Check:
    """单笔仓位占比 vs 风控单仓上限。**近似判据**，所以措辞必须说清近似在哪里。

    风控比的是 `price × qty / 账户 USDT 余额`（`RiskGuard._check_position_limit`），而策略
    多半按 `总资金 × position_pct` 算量——两边只在「总资金 == 余额」时严格相等。所以这里
    只做**量级**判断：占比明显超过上限时，第一根 bar 就会被 `hard_limit` 拒，并计一次
    risk_hits；贴着上限时提示一下。

    单独抽成一个只吃两个值的函数，是为了让它能被用例直接钉住——否则这条判据只能靠一张
    带真实策略、真实 registry 的夹具来验，那验的是别的东西。
    """
    from apps.riskguard.guard import RiskGuard

    if position_pct is None:
        return _check(
            "risk.position_cap",
            "单笔仓位不超风控上限",
            SKIP,
            "会话参数里读不到 position_pct（该策略可能按 quantity 下单）",
        )

    cap = RiskGuard.MAX_POSITION_RATIO_PERP if perp else RiskGuard.MAX_POSITION_RATIO
    kind = "合约" if perp else "现货"
    # 转成 Decimal 再比：`cap` 是 Decimal（风控的类常量），而 `position_pct` 从
    # `params_schema` 读出来是 float，`Decimal * float` 会当场 TypeError——那种报错
    # 出现在体检里看起来像「体检坏了」，不像「比较口径没对齐」。
    # 走 `str()` 而不是 `Decimal(float)`：后者把二进制误差原样带进来。
    pct = Decimal(str(position_pct))
    detail = (
        f"position_pct={position_pct:.4g}，{kind}单仓上限 {cap}"
        "（近似判据：按「总资金 ≈ 账户余额」比较）"
    )
    if pct > cap:
        return _check(
            "risk.position_cap",
            "单笔仓位不超风控上限",
            FAIL,
            detail
            + "——超过上限的单会被 RiskGuard 拒（hard_limit），并计一次 paper 风控命中",
        )
    if pct > cap * _TIGHT_RATIO:
        return _check(
            "risk.position_cap",
            "单笔仓位不超风控上限",
            WARN,
            detail + "——贴着上限，账户余额略低于总资金时就会被拒",
        )
    return _check("risk.position_cap", "单笔仓位不超风控上限", PASS, detail)


def _risk_checks(session: Any) -> list[Check]:
    from apps.trading import session_params

    try:
        table = session_params.effective_params(session.strategy, session.config or {})
    except Exception as exc:  # noqa: BLE001 - 策略类解析不到是外部状态，不是失败
        return [
            _check(
                "risk.position_cap",
                "单笔仓位不超风控上限",
                SKIP,
                f"生效参数解析不出来：{exc}",
            )
        ]

    if not table.get("resolved"):
        return [
            _check(
                "risk.position_cap",
                "单笔仓位不超风控上限",
                SKIP,
                f"策略类「{session.strategy.name}」在本进程里解析不到，读不到生效参数",
            )
        ]

    effective = {row["key"]: row.get("effective") for row in table.get("rows", [])}
    raw = effective.get("position_pct")
    try:
        pct = None if raw is None else float(raw)
    except (TypeError, ValueError):
        pct = None
    perp = session.symbol.upper().endswith("-SWAP")
    return [risk_cap_check(session.symbol, pct, perp=perp)]


def _runtime_checks(session: Any) -> list[Check]:
    """运行时：beat 四条、以及 paper runner 是否还在 Redis 里跑着。"""
    checks: list[Check] = []

    try:
        entries, declared = _beat_entries()
    except Exception as exc:  # noqa: BLE001 - 导不进 celery_app 是环境状态
        checks.append(
            _check("runtime.beat", "关键 beat 条目在跑", SKIP, f"读不到调度表：{exc}")
        )
    else:
        from datetime import timezone as _tz

        from apps.regime import beat_health

        found = beat_health.findings(entries, declared, now=datetime.now(_tz.utc))
        bad = [f for f in found if f.name in CRITICAL_BEAT_NAMES]
        if not entries:
            checks.append(
                _check(
                    "runtime.beat",
                    "关键 beat 条目在跑",
                    SKIP,
                    "PeriodicTask 表里一行都没有（beat 没合并过 / 本环境没有调度数据）",
                )
            )
        elif bad:
            checks.append(
                _check(
                    "runtime.beat",
                    "关键 beat 条目在跑",
                    WARN,
                    "；".join(f"{f.name}：{f.problem.display}" for f in bad)
                    + "。注意 beat_schedule 只在 beat 启动时合并进 PeriodicTask 一次",
                )
            )
        else:
            checks.append(
                _check(
                    "runtime.beat",
                    "关键 beat 条目在跑",
                    PASS,
                    "、".join(CRITICAL_BEAT_NAMES),
                )
            )

    alive = _frame_runner_alive(str(session.pk))
    if alive is None:
        checks.append(
            _check(
                "runtime.paper_runner",
                "paper runner 已卸下",
                SKIP,
                "读不到 Redis 兼容层键（redis 不可达 / 不是交易进程）",
            )
        )
    elif alive:
        checks.append(
            _check(
                "runtime.paper_runner",
                "paper runner 已卸下",
                WARN,
                "Redis 里 frame:live_session 仍写着 running。promote **不拆 runner**："
                "升级后它会继续往 trading:orders 投单（订单挂在一条已 stopped 的会话上）"
                "直到进程重启。执行 promote 前先 POST /sessions/<id>/pause/",
            )
        )
    else:
        checks.append(
            _check(
                "runtime.paper_runner",
                "paper runner 已卸下",
                PASS,
                "兼容层键不在 running",
            )
        )
    return checks


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #


def preflight(
    *,
    paper_session: Any,
    live_account: Any | None = None,
    now: datetime | None = None,
) -> Report:
    """对一条 paper 会话做完整体检。**只读**。

    `now` 可注入（与 `effects.read_paper` / `effects.read_regime` 同一条纪律）：观察期与
    「在期豁免」都是相对现在的量，不给注入点就没法钉「恰 14 天」「恰在到期那一刻」这两条
    边界，用例只能靠真实时钟、断言随运行时刻漂移。
    """
    from django.utils import timezone

    at = now or timezone.now()

    checks: list[Check] = []
    checks.extend(_session_checks(paper_session))
    candidate_checks, candidate = _candidate_checks(paper_session)
    checks.extend(candidate_checks)
    if candidate is not None:
        checks.extend(_paper_checks(candidate, now=at))
    checks.extend(_regime_checks(candidate))
    checks.extend(_live_account_checks(paper_session, live_account))
    checks.extend(_halt_checks(paper_session, now=at))
    checks.extend(_risk_checks(paper_session))
    checks.extend(_runtime_checks(paper_session))

    return Report(
        paper_session_id=str(paper_session.pk),
        candidate_id=None if candidate is None else str(candidate.pk),
        checks=tuple(checks),
    )


def find_paper_session(paper_session_id: str) -> Any | None:
    """按 id 取一条 `mode="paper"` 会话；不是模拟盘或不存在都返回 `None`。"""
    from apps.trading.models import LiveSession

    return (
        LiveSession.objects.filter(pk=paper_session_id, mode="paper")
        .select_related("strategy", "exchange_account", "backtest_result", "user")
        .first()
    )


def find_live_account(account_id: str) -> Any | None:
    from apps.exchange.models import ExchangeAccount

    return ExchangeAccount.objects.filter(pk=account_id).first()


def scan(*, now: datetime | None = None) -> list[Report]:
    """把**还没到终态**的候选逐条体检一遍（`--scan` 用）。

    范围取「非终态」：`regime_hold` 的候选要人知道它在等什么，`in_sample_failed` 也在等
    一次人工决定。终态（live/rejected/cancelled）不进来——它们没有下一步可体检。

    没有 paper 会话的候选落 `applicable=False`：那是「还没到 L3」而不是「体检不合格」，
    `--scan` 要能说清这条候选现在在哪一层，同时不该让 `--strict` 把它判成不通过。
    """
    from apps.strategy_factory.models import StrategyCandidate
    from apps.strategy_factory.status import is_terminal

    reports: list[Report] = []
    queryset = StrategyCandidate.objects.select_related(
        "strategy", "paper_session", "paper_session__exchange_account"
    ).order_by("created_at")
    for candidate in queryset:
        if is_terminal(candidate.status_enum):
            continue
        session = candidate.paper_session
        if session is None:
            reports.append(
                Report(
                    paper_session_id=None,
                    candidate_id=str(candidate.pk),
                    applicable=False,
                    checks=(
                        _check(
                            "candidate.row",
                            "存在候选主行",
                            PASS,
                            f"{candidate.name}，status={candidate.status_enum.display}",
                        ),
                        _check(
                            "paper.evidence",
                            "paper 层证据可采",
                            SKIP,
                            "这条候选还没有 paper 会话（还没走到 L3）",
                        ),
                    ),
                )
            )
            continue
        reports.append(preflight(paper_session=session, now=now))
    return reports
