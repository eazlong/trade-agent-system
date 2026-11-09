"""回归测试：网格搜索**不得**在内存里留住逐 bar / 逐笔的全量列表。

线上现象（txy，2026-11）：celery 容器内存高位且不回落。

根因之一在 `run_grid_search_task`：每个组合回测完都
`results.append({"result_id": rid, "params": params, **stats})`，而 `stats` 里
`equity_curve`（逐 bar）、`trades`（逐笔）、`benchmark.curve`（逐 bar）都是全量
列表。`results` 要留到组合循环**跑完**才消费（排序、best_result、切片投递都在
循环之后），于是 100 个组合的全量曲线同时在内存里。

实测规模：1 年 15m 的 `equity_curve` 是 7.2MB / 35040 点；100 个组合同时驻留
是 650MB 以上。而这些数据在 `save_backtest_result` 时**本来就截断到 2000 点**
写库，内存里这份全量副本对任何下游都没有用处——下游只读 `result_id`、`params`
和 `sort_by` 认的那个标量。

这里钉住的就是这条：留在内存里的 `stats` 只允许标量。
"""

from apps.backtest.tasks import _GRID_RESULT_SCALAR_KEYS, _scalar_stats


def _stats_with_big_lists(bars: int = 35040, trades: int = 500) -> dict:
    """一份形状与 `BacktestEngine._compute_stats()` 返回值一致的大对象。"""
    return {
        "final_equity": 12345.67,
        "total_return_pct": 23.45,
        "max_drawdown_pct": -8.1,
        "win_rate": 0.55,
        "sharpe_ratio": 1.42,
        "total_trades": trades,
        "profit_factor": 1.8,
        "payoff_ratio": 1.2,
        "cost_detail": {"commission": 100.0},
        "equity_curve": [
            {
                "timestamp": f"2026-01-01T00:00:{i % 60:02d}+00:00",
                "equity": 10000.0 + i,
                "drawdown": -0.01,
            }
            for i in range(bars)
        ],
        "trades": [
            {
                "side": "buy",
                "entry_price": 100.0,
                "exit_price": 101.0,
                "quantity": 0.01,
                "pnl": 0.5,
                "exit_reason": "take_profit",
            }
            for _ in range(trades)
        ],
        "benchmark": {
            "name": "Buy & Hold",
            "final_value": 11000.0,
            "return_pct": 10.0,
            "curve": [
                {"timestamp": f"2026-01-01T00:00:{i % 60:02d}+00:00", "equity": 10000.0}
                for i in range(bars)
            ],
        },
    }


class TestScalarStatsDropsBigLists:
    """逐 bar / 逐笔的列表必须被丢掉。"""

    def test_equity_curve_is_dropped(self):
        out = _scalar_stats(_stats_with_big_lists())
        assert "equity_curve" not in out

    def test_trades_are_dropped(self):
        out = _scalar_stats(_stats_with_big_lists())
        assert "trades" not in out

    def test_benchmark_curve_is_dropped(self):
        """`benchmark` 整块丢掉：它唯一的大字段是 curve，其余是重复的标量。"""
        out = _scalar_stats(_stats_with_big_lists())
        assert "benchmark" not in out

    def test_scalars_survive(self):
        out = _scalar_stats(_stats_with_big_lists())
        assert out["sharpe_ratio"] == 1.42
        assert out["total_return_pct"] == 23.45
        assert out["win_rate"] == 0.55
        assert out["final_equity"] == 12345.67
        assert out["total_trades"] == 500

    def test_result_is_small_regardless_of_bar_count(self):
        """留在内存里的对象大小与 K 线根数无关——这是这条修复的全部意义。"""
        small = _scalar_stats(_stats_with_big_lists(bars=100, trades=10))
        large = _scalar_stats(_stats_with_big_lists(bars=100_000, trades=5000))

        # 键集合完全相同：换一份更长的行情不该多留任何键
        assert set(small) == set(large)
        # 值也必须是同一个标量，不能是「被截断的列表」
        assert large["sharpe_ratio"] == small["sharpe_ratio"]
        for value in large.values():
            assert not isinstance(value, (list, dict)), (
                f"`_scalar_stats` 留下了非标量值 {type(value)}；"
                "内存占用会随 K 线根数线性增长"
            )


class TestScalarStatsKeepsSortKey:
    """`sort_by` 认的那个键必须留下来，否则 best_result 恒为第一个组合。"""

    def test_custom_sort_key_is_kept(self):
        stats = {"my_custom_metric": 3.14, "equity_curve": []}
        out = _scalar_stats(stats, sort_key="my_custom_metric")
        assert out["my_custom_metric"] == 3.14
        assert "equity_curve" not in out

    def test_directional_sort_key_prefix_is_stripped(self):
        """`sort_by` 允许 `-sharpe_ratio` 这种带方向的写法。"""
        stats = {"sharpe_ratio": 1.42, "equity_curve": []}
        out = _scalar_stats(stats, sort_key="-sharpe_ratio")
        assert out["sharpe_ratio"] == 1.42

    def test_absent_sort_key_does_not_crash(self):
        out = _scalar_stats({"sharpe_ratio": 1.0}, sort_key="not_a_key")
        assert "not_a_key" not in out
        assert out["sharpe_ratio"] == 1.0

    def test_whitelist_is_all_scalars_by_name(self):
        """白名单里不许出现逐 bar / 逐笔字段名。"""
        forbidden = {"equity_curve", "trades", "drawdown_curve", "curve", "benchmark"}
        assert not (forbidden & _GRID_RESULT_SCALAR_KEYS)
