"""币安 USD-M 合约的「用户口径品种名 → 合约名 + 乘数」映射。

## 为什么需要这一层（2026-11-27 txy 事故）

paper 会话按**现货口径**配置品种（`PEPE/USDT`：数量按 PEPE 个数、价格按每 1 个
PEPE），而执行走 USD-M 合约。币安合约把这类小币种按「每张 = 1000 个基础币」上市：
合约里叫 `1000PEPEUSDT`，**根本没有** `PEPEUSDT`。适配器把 `PEPEUSDT` 原样拿去
exchangeInfo 校验 → 规则表里没有它 → 拒单（fail-closed 本身是对的，没有把未归一化
的数量发出去），但这条策略从此永远出不了单，且持仓对账按 `PEPEUSDT` 精确匹配
永远匹配不到 `1000PEPEUSDT` → 台账每小时被下钳成 0。

## 这一层只做换算，不做猜测

* 数量：**合约张数 = 用户口径数量 ÷ 乘数**（下单前），反向 **× 乘数**（成交/持仓）。
* 价格：**合约价 = 用户口径价 × 乘数**，反向 **÷ 乘数**
  （合约价是「每 1 张」，用户口径价是「每 1 个基础币」）。
* 名义价值两边相等（数量与价格反向缩放），所以风控的名义敞口算式不用改。

**映射表是显式表，绝不按名字前缀推断**：`1INCH`、`0G`、`2Z`、`4`、`42` 这些是
真实币名，一旦被当成「1 个合约 = 1000 个基础币」就会把数量放大 1000 倍。表里没
收录、且合约表里也没有的品种保持现状：适配器在 exchangeInfo 校验处 fail-closed，
并在报错里让用户确认合约名。

## 维护方式

只登记「**币安现货名不带数字前缀、合约名带**」的品种。现货本身就叫 `1000SATS`、
`1MBABYDOGE` 这类的，用户本来就能按合约名配置（数量与价格已经在合约口径），
**不要**登记——登记了会二次缩放。新增一条前先在交易所两类市场表里核对两个名字。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

__all__ = [
    "CONTRACT_SPECS",
    "ContractSymbol",
    "from_contract",
    "split_symbol",
    "to_contract",
]


#: 合约 symbol 的计价币后缀。按**长的优先**匹配，避免 `USDT` 抢在 `USDTB` 之类前面。
QUOTE_ASSETS: tuple[str, ...] = (
    "USDT",
    "USDC",
    "FDUSD",
    "BUSD",
    "TUSD",
    "USDP",
    "DAI",
    "BTC",
    "ETH",
    "BNB",
)

#: 用户口径基础币 → (合约基础币, 乘数)。乘数 = **1 张合约代表多少个基础币**。
#:
#: 只收录「现货名不带数字前缀」的品种（见模块 docstring 的维护说明）：
#: 币安现货是 `PEPEUSDT`，USD-M 合约是 `1000PEPEUSDT`。
#:
#: **乘数必须是 10 的幂**（1000 / 1000000）：数量换算靠十进制精确除法与整除取整，
#: 非 10 的幂会引入循环小数、并在取整时改变下单量。新增一条前先在两类市场表里核对。
CONTRACT_SPECS: dict[str, tuple[str, Decimal]] = {
    "PEPE": ("1000PEPE", Decimal("1000")),
    "SHIB": ("1000SHIB", Decimal("1000")),
    "LUNC": ("1000LUNC", Decimal("1000")),
    "FLOKI": ("1000FLOKI", Decimal("1000")),
    "BONK": ("1000BONK", Decimal("1000")),
    "XEC": ("1000XEC", Decimal("1000")),
}

#: 合约基础币 → (用户口径基础币, 乘数)：反向映射，供持仓/成交翻译回用户口径。
_VENUE_TO_USER: dict[str, tuple[str, Decimal]] = {
    contract_base: (user_base, mult)
    for user_base, (contract_base, mult) in CONTRACT_SPECS.items()
}


@dataclass(frozen=True)
class ContractSymbol:
    """一次解析的结果：交易所口径的 symbol 与两个口径之间的乘数。

    ```python
    to_contract("PEPE/USDT")      # venue=1000PEPEUSDT, multiplier=1000
    to_contract("1000PEPE/USDT")  # venue=1000PEPEUSDT, multiplier=1（已是合约口径）
    to_contract("BTC/USDT")       # venue=BTCUSDT,      multiplier=1
    ```
    """

    #: 交易所口径的下单 symbol（无斜杠，如 `1000PEPEUSDT`）。
    venue_symbol: str
    #: 1 张合约 = 多少个基础币。**1 表示用户口径与合约口径一致**（无需换算）。
    multiplier: Decimal
    #: 用户口径的基础币（`PEPE`）。配置里若直接写合约名，这里是它对应的用户口径名。
    user_base: str
    #: 计价币（`USDT`）；识别不出来时为空串。
    quote: str

    @property
    def needs_conversion(self) -> bool:
        return self.multiplier != 1


def split_symbol(symbol: str) -> tuple[str, str, str]:
    """拆 `PEPE/USDT`、`PEPEUSDT`、`pepe/usdt:usdt` 这类写法。

    Returns:
        `(base, quote, sep)`。识别不出计价币时 `quote` 为空串——**不做猜测**：
        后面 `to_contract` 会把整串原样当 base 用，是否合法由 exchangeInfo 去判。
    """
    raw = (symbol or "").strip().upper()
    if "/" in raw:
        base, _, quote = raw.partition("/")
        # ccxt 风格的结算币后缀（`PEPE/USDT:USDT`），本系统不用，但不要把它当成 base。
        quote = quote.split(":")[0]
        return base, quote, "/"
    for quote in sorted(QUOTE_ASSETS, key=len, reverse=True):
        if len(raw) > len(quote) and raw.endswith(quote):
            return raw[: -len(quote)], quote, ""
    return raw, "", ""


def to_contract(symbol: str) -> ContractSymbol:
    """用户口径 symbol → 合约口径 symbol + 乘数。

    三种情形：

    1. base 在乘数表里（`PEPE`）→ `1000PEPEUSDT`，乘数 1000；
    2. base **就是**合约名（`1000PEPE`）→ 同一个合约、**同一个乘数**：本系统里数量
       只有一种口径（用户口径 = 基础币个数），两种写法的含义完全相同；
    3. 都不是（`BTC`、`1INCH`、`1000SATS`、`FOO`）→ 原样，乘数 1，由 exchangeInfo
       判定存不存在（`1000SATS` 这类现货与合约同名的品种**不能**登记进表，否则
       会把数量二次缩放）。
    """
    base, quote, _ = split_symbol(symbol)
    if base in CONTRACT_SPECS:
        contract_base, multiplier = CONTRACT_SPECS[base]
        return ContractSymbol(
            venue_symbol=f"{contract_base}{quote}",
            multiplier=multiplier,
            user_base=base,
            quote=quote,
        )
    if base in _VENUE_TO_USER:
        user_base, multiplier = _VENUE_TO_USER[base]
        return ContractSymbol(
            venue_symbol=f"{base}{quote}",
            # **与 from_contract 严格互逆**：系统里只有一种数量口径（基础币个数），
            # 用户写 PEPE/USDT 还是 1000PEPE/USDT 表达的是同一个品种的同一笔单。
            # 若这里取 1，写路径按「张」直发、读路径却按「个」换算，同一笔仓位在
            # 台账上会差 1000 倍（review 2026-11-27 抓到的写读不对称）。
            multiplier=multiplier,
            user_base=user_base,
            quote=quote,
        )
    return ContractSymbol(
        venue_symbol=f"{base}{quote}",
        multiplier=Decimal("1"),
        user_base=base,
        quote=quote,
    )


def contract_style_error(symbol: str) -> str | None:
    """配置写成**合约名**（`1000PEPE/USDT`）时返回拒绝原因；写法正常返回 `None`。

    本系统只有一种数量口径：用户口径 = 基础币个数，价格取自**该字面 symbol 的现货
    K 线**。把配置写成合约名会同时坏两件事：现货 K 线取不到；即使拿到（每 1000 个
    的价），按价定量的结果会被适配层再除一次乘数，名义只剩意图的 1/1000。
    正确写法是按现货名配置，换算交给适配层（2026-11-27 review）。
    """
    base, quote, _ = split_symbol(symbol)
    if base in _VENUE_TO_USER:
        user_base, multiplier = _VENUE_TO_USER[base]
        suggested = f"{user_base}/{quote}" if quote else user_base
        return (
            f"品种应按现货名配置（{symbol} → {suggested}）：{base} 是合约口径的名字"
            f"（1 张 = {multiplier} 个 {user_base}），按它配置会让下单数量与 K 线口径"
            "差一个乘数；合约名与乘数的换算由适配层负责"
        )
    return None


def from_contract(venue_symbol: str) -> tuple[str, Decimal]:
    """合约口径 symbol → `(用户口径 symbol, 乘数)`，供持仓/成交翻译回用户口径。

    `from_contract("1000PEPEUSDT")` → `("PEPEUSDT", 1000)`；
    `from_contract("BTCUSDT")` → `("BTCUSDT", 1)`。
    乘数表里没有的品种恒等返回，绝不擅自剥掉数字前缀（`1000SATS` 的用户口径**就是**
    `1000SATS`，剥掉就变成一个不存在的品种）。

    **一对一的假设**：若交易所某天同时上市 `LUNCUSDT` 与 `1000LUNCUSDT`，两者会塌成
    同一个用户口径名。持仓是按 key 汇总的，届时会互相覆盖——真出现时必须在表里加
    「同名并存」的显式处理，不能照旧。
    """
    base, quote, _ = split_symbol(venue_symbol)
    if base in _VENUE_TO_USER:
        user_base, multiplier = _VENUE_TO_USER[base]
        return f"{user_base}{quote}", multiplier
    return f"{base}{quote}", Decimal("1")
