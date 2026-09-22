"""行情阶段机制的持久化模型。

本模块只落**原始事实**，不落任何标签：日线标签是判定算法的确定性函数，落库会
制造第二个真相——算法一改，库里那批标签就是错的，而它们看起来完全正常，这是最
难发现的一类错误（CONTEXT.md 决策）。
"""

from __future__ import annotations

from decimal import Decimal

from django.db import models

# 必须与下面 DecimalField 的 decimal_places 一致：写入方按这个精度量化后再落库，
# 否则「刚从交易所解析出的值」与「从库里读回的值」不相等，幂等同步会每次运行都
# 报「已更新」——同步看起来在工作，实际上一直在重写同一批数据。
DECIMAL_QUANTUM = Decimal("0.00000001")


class DailyCandle(models.Model):
    """一枚**已收盘**的日线，只存原始 OHLCV。

    两条刻意的取舍：

    1. **不存标签、不存派生指标**。阶段标签由判定算法从原始 OHLCV 现算；存了就
       等于把「日期 → 阶段」标签表从唯一真相降级成两份可能互相矛盾的答案。

    2. **同时存 `open_time` 与 `date`，明知冗余**。`open_time` 是交易所返回的原始
       时刻（Binance 日线固定 00:00 UTC 开盘），`date` 是它与判定/切片共用的自然日
       键。留 `date` 是为了让「日期 → 阶段」的关联是一次等值查询而不是时区换算，
       并把「业务日 = UTC 开盘日 = 北京时间同一日」这层口径写在表上，而不是留给读者
       自己推。两者由同一个写入方（candles.py）从同一个毫秒时间戳导出，
       `date == open_time.astimezone(utc).date()` 由测试钉住。
    """

    symbol = models.CharField("标的", max_length=32)
    date = models.DateField("自然日（UTC 开盘日 = 北京时间同日）")
    open_time = models.DateTimeField("开盘时刻（UTC）")

    open = models.DecimalField("开盘价", max_digits=20, decimal_places=8)
    high = models.DecimalField("最高价", max_digits=20, decimal_places=8)
    low = models.DecimalField("最低价", max_digits=20, decimal_places=8)
    close = models.DecimalField("收盘价", max_digits=20, decimal_places=8)
    volume = models.DecimalField(
        "成交量（基础币）", max_digits=30, decimal_places=8
    )

    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        db_table = "regime_daily_candles"
        verbose_name = "日线（原始 OHLCV）"
        verbose_name_plural = "日线（原始 OHLCV）"
        ordering = ["symbol", "date"]
        constraints = [
            models.UniqueConstraint(
                fields=["symbol", "date"], name="uniq_daily_candle_symbol_date"
            )
        ]

    def __str__(self) -> str:
        return f"{self.symbol} {self.date} close={self.close}"
