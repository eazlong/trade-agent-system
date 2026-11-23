# Generated for issue #66
"""格子轴从四档换成 cycle 三格：三个 `regime` 列的取值集合 + 存量行迁移。

## 为什么 `choices` 是并集

`RegimePoolCell` / `DeactivationDecision` / `DeactivationExemption` 的 `regime` 列现在存的是
**格子轴的键**。新世代写 `bull` / `bear` / `sideways`，而库里还有大量四档行（第 5 代之前的
池化格、换轴之前写下的决策与豁免）。`choices` 因此是两套键集的并集：它只是表单校验与文档，
并集才不撒谎；`ALTER COLUMN` 在 SQLite/PostgreSQL 上都不改数据，两类行各自读得懂。

## 决策与豁免**必须**迁移，池化格**不迁移**

这两半的处置相反，理由不同：

- **决策与豁免要迁。** 它们的键参与「此刻该不该拦」的求值：`gate_run._warrant` 拿
  `(策略, decision.regime)` 去新代里找那一格，而新代没有 `downtrend` 这一格——找不到就判
  `LAPSED`，`gate._close_reason` 记 `regime_left`，活声明被解除。**解除的方向是放开开仓**，
  于是一次换轴会成为一次静默放开。豁免同理：`close_left_regime_exemptions` 用 `!=` 比对，
  一条登记在 `downtrend` 的在期豁免会在换轴当天被当成「阶段离开」关掉，而 `closed_at` 写下去
  没有复活路径。
- **池化格不迁。** 每一代池化格是「那一代算出了什么」的产物，审计要的就是那个原值；而读路径
  永远按 `rebuild` 外键过滤，新旧世代不会混读。改写它等于篡改历史，且没有任何求值依赖它。

## `high_vol` 不映射

`high_vol` 在旧结构里是「保命档独占一格」，那天**没有任何方向结论**（`cycle` 留 NULL 是
迁移 0018 的同一条理由）。给它编一个 `sideways` 会把「那天判不出来」伪造成「那天是横盘」，
而这条伪造会直接参与停用决策。

结构上 `high_vol` 决策不该存在（池化对高 volatility 那一格恒返回 `blanket`，而 `blanket`
不是 `TARGET`），所以正常情况下这个分支匹配零行；**真匹配到时如实留原值并让它自然过期**
——`_warrant` 会判 LAPSED、记一条 `became_fit` 解除原因，那是可追溯的收场，比猜一个方向好。

## 留痕

决策行的原值写进 `evidence["legacy_regime"]`。这条不是可选的美化：换轴之后
「为什么当初按 `downtrend` 停我」必须仍能从这一行答出来，而 `regime` 列已经被改成 `bear`。

## 反向

反向迁移把**迁移过的**值映射回去（`bear→downtrend` / `bull→uptrend` / `sideways→range`），
并删掉 `evidence["legacy_regime"]`。它不是无损的：新世代本来就写三格，回滚会把它们一并
改写成四档。回滚手册应当写明这一点——与 0018 的「回滚后再前进要重跑回填」同一条纪律。
"""

from django.db import migrations, models

#: 四档 → 三格。**刻意不复用 `models._LEGACY_TO_CYCLE`**：迁移必须能独立重放，而 import
#: 应用代码会让历史迁移随代码漂移（0018 的 `_LEGACY_MAPPING` 同一条理由，同一处刻意重复）。
_FOUR_TO_CYCLE = {
    "uptrend": "bull",
    "downtrend": "bear",
    "range": "sideways",
}

#: 三格 → 四档（反向用）。
_CYCLE_TO_FOUR = {cycle: four for four, cycle in _FOUR_TO_CYCLE.items()}

#: 并集的选择项，与 `models.CELL_REGIME_CHOICES` 逐项相同。迁移里写死一份，理由同上。
_CELL_CHOICES = [
    ("bull", "上行周期"),
    ("bear", "下行周期"),
    ("sideways", "横盘"),
    ("high_vol", "高波动"),
    ("downtrend", "下行趋势"),
    ("uptrend", "上行趋势"),
    ("range", "箱体震荡"),
]


def _migrate_forward(apps):
    """决策与豁免按表映射；决策额外留痕。返回 (迁移条数, 未映射条数)。"""
    Decision = apps.get_model("regime", "DeactivationDecision")
    Exemption = apps.get_model("regime", "DeactivationExemption")

    migrated = unmapped = 0
    for four, cycle in _FOUR_TO_CYCLE.items():
        for decision in Decision.objects.filter(regime=four):
            evidence = dict(decision.evidence or {})
            # 只在还没有留痕时写：重复跑（数据库被回滚过又前进）不该把第一次的原值覆盖掉。
            evidence.setdefault("legacy_regime", four)
            decision.regime = cycle
            decision.evidence = evidence
            decision.save(update_fields=["regime", "evidence"])
            migrated += 1
        migrated += Exemption.objects.filter(regime=four).update(regime=cycle)

    # `high_vol`：结构上不该有；真有就留着原值，让它按 `_warrant` 的 LAPSED 收场。
    unmapped = Decision.objects.filter(regime="high_vol").count()
    unmapped += Exemption.objects.filter(regime="high_vol").count()
    return migrated, unmapped


def migrate_cells(apps, schema_editor):
    """前向：把决策与豁免的格子键换成 cycle。**不动池化格**，理由见模块 docstring。

    ## 不映射的那些行必须被看见

    `_migrate_forward` 回来的 `unmapped` 是表里仍是 `high_vol` 的行数。不打印它，那批行
    会在某一次 `close_left_regime_exemptions` 或 `_warrant` 里被**静默**收场：豁免被写成
    「阶段离开」，决策被判 `LAPSED` 并解除。两种收场都会留下痕迹，但「迁移时就知道有
    几条」与「事后从日志里倒推」是两件事。

    所以这里把计数写成一条 warning。不 `raise`：那批行是**真实的历史**（`high_vol` 豁免
    可以被显式发放，「显式就是知情」），让一次数据迁移因此失败，等于拿升级挡住运行；
    而它们各自的收场是可追溯的。
    """
    migrated, unmapped = _migrate_forward(apps)
    if unmapped:
        import logging

        logging.getLogger(__name__).warning(
            "[migration 0019] %d 条决策/豁免的格子键是 high_vol，未映射到 cycle："
            "它们会在下一次推导/重放时按「该格子已不存在」收场"
            "（豁免记为阶段离开、决策记 LAPSED）。这是如实收场而非数据丢失——"
            "高波动独占一格的口径已经不存在了。",
            unmapped,
        )
    elif not migrated:
        import logging

        logging.getLogger(__name__).info("[migration 0019] 没有需要迁移的四档格子键")


def revert_cells(apps, schema_editor):
    """反向：把三格映射回四档并删掉留痕。**不是无损的**，见模块 docstring 末节。"""
    Decision = apps.get_model("regime", "DeactivationDecision")
    Exemption = apps.get_model("regime", "DeactivationExemption")
    for cycle, four in _CYCLE_TO_FOUR.items():
        for decision in Decision.objects.filter(regime=cycle):
            evidence = dict(decision.evidence or {})
            evidence.pop("legacy_regime", None)
            decision.regime = four
            decision.evidence = evidence
            decision.save(update_fields=["regime", "evidence"])
        Exemption.objects.filter(regime=cycle).update(regime=four)


class Migration(migrations.Migration):
    dependencies = [
        ("regime", "0018_two_axis_cycle_volatility"),
    ]

    operations = [
        migrations.AlterField(
            model_name="regimepoolcell",
            name="regime",
            field=models.CharField(
                choices=_CELL_CHOICES,
                max_length=16,
                verbose_name="格子（周期方向；旧世代为四档）",
            ),
        ),
        migrations.AlterField(
            model_name="deactivationdecision",
            name="regime",
            field=models.CharField(
                choices=_CELL_CHOICES,
                max_length=16,
                verbose_name="格子（周期方向；旧决策为四档）",
            ),
        ),
        migrations.AlterField(
            model_name="deactivationexemption",
            name="regime",
            field=models.CharField(
                choices=_CELL_CHOICES,
                max_length=16,
                verbose_name="格子（周期方向；已迁移旧四档）",
            ),
        ),
        migrations.RunPython(migrate_cells, revert_cells),
    ]
