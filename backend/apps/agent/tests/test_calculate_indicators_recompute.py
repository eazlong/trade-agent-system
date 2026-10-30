"""回归测试：calculate_indicators 在会话内可重复调用（EMA50/EMA200 重算）。

事故（生产）：会话内先算 RSI，随后要重算 EMA50/EMA200 时，同一 ``temp_file``
的后续调用全部报「临时文件不存在」，连续失败 3 次触发 ``_run_tool_loop`` 的
同工具熔断（``_TOOL_FAILURE_DISABLE_THRESHOLD``），该工具在会话剩余时间里被禁用。

根因：``CalculateIndicatorsTool.execute()`` 结尾 ``path.unlink()`` 把 fetch_ohlcv
生成的 K 线文件删掉了。fetch_ohlcv 的返回契约是「数据写入临时文件，路径供
calculate_indicators 读取」——它是一次抓取的共享产物，不是单次计算的私有输入。
第一次调用吃掉文件，第二次就必然失败。

接缝：直接走 ``CalculateIndicatorsTool.execute()`` 真实链路（真实 pandas_ta、
真实 JSON 文件），断言「同一个 temp_file 能连续用两次」——这正是事故发生时的
调用形态。box_range 也读同一个 temp_file，所以一并钉住。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pandas_ta as ta  # noqa: F401  —— 注册 DataFrame.ta accessor
import pytest

from apps.agent.tools.market_data import CalculateIndicatorsTool

pytestmark = pytest.mark.asyncio


def _write_ohlcv(path: Path, n: int = 300) -> Path:
    """写一份与 FetchOHLCVTool 落盘格式一致的 K 线 JSON。"""
    rows = []
    price = 100.0
    for i in range(n):
        price += (i % 7) - 3
        rows.append(
            {
                "timestamp": f"2026-01-{(i % 28) + 1:02d}T00:00:00",
                "open": price,
                "high": price + 2,
                "low": price - 2,
                "close": price + 1,
                "volume": 1000.0 + i,
            }
        )
    path.write_text(json.dumps(rows), encoding="utf-8")
    return path


async def test_same_temp_file_can_be_used_twice(tmp_path):
    """核心回归：一次 fetch_ohlcv 的产物必须支撑多次指标计算。

    事故形态：先算 rsi，再重算 ema50/ema200 —— 第二次不得报「临时文件不存在」。
    """
    temp_file = _write_ohlcv(tmp_path / "ohlcv.json")
    tool = CalculateIndicatorsTool()

    first = await tool.execute(temp_file=str(temp_file), indicators=["rsi"])
    assert first.success is True, f"首次计算失败：{first.error!r}"

    # 文件必须还在（fetch_ohlcv 的契约是共享产物，不是一次性输入）
    assert temp_file.exists(), "calculate_indicators 不应删除 fetch_ohlcv 的 K 线文件"

    second = await tool.execute(
        temp_file=str(temp_file),
        indicators=["ema"],
        params={"ema": {"lengths": [50, 200]}},
    )
    assert second.success is True, (
        f"同一 temp_file 的第二次调用失败（事故根因）：{second.error!r}"
    )
    ema = second.data.get("ema") or {}
    assert ema.get("ema50") is not None, f"EMA50 未算出：{ema}"
    assert ema.get("ema200") is not None, f"EMA200 未算出：{ema}"


async def test_box_range_can_still_read_the_file_after_indicators(tmp_path):
    """同根因的连带影响：box_range 也读同一个 temp_file。

    指标计算把文件删掉后，``detect_box_range(klines=temp_file)`` 同样报路径不存在。
    """
    from apps.agent.tools.box_range import DetectBoxRangeTool

    temp_file = _write_ohlcv(tmp_path / "ohlcv.json")

    calc = await CalculateIndicatorsTool().execute(
        temp_file=str(temp_file), indicators=["rsi"]
    )
    assert calc.success is True, f"指标计算失败：{calc.error!r}"

    box = await DetectBoxRangeTool().execute(klines=str(temp_file), max_width_pct=0.5)
    assert box.success is True, (
        f"指标计算后 box_range 读不到同一 temp_file（连带事故）：{box.error!r}"
    )


async def test_ema_length_param_is_honoured(tmp_path):
    """``params={"ema": {"length": 50}}`` 必须真的只算 EMA50。

    原实现只认 ``lengths``，把 ``length`` 悄悄丢掉并回退到默认三档，
    于是 ema20/ema50/ema200 三个值完全相同——静默算错，比报错更危险。
    """
    temp_file = _write_ohlcv(tmp_path / "ohlcv.json")

    res = await CalculateIndicatorsTool().execute(
        temp_file=str(temp_file),
        indicators=["ema"],
        params={"ema": {"length": 50}},
    )
    assert res.success is True, f"计算失败：{res.error!r}"
    ema = res.data.get("ema") or {}
    assert "ema50" in ema, f"length=50 未被采纳，实际返回：{ema}"
    assert set(ema) == {"ema50"}, f"length=50 时不应算出其它周期：{ema}"


async def test_caller_params_dict_is_not_mutated(tmp_path):
    """调用方传入的 params 不得被就地改写。

    原实现 ``indicator_params.pop("lengths", ...)`` 直接把调用方的 dict 改了：
    同一份 params 第二次使用时 ``lengths`` 已消失，静默退回默认三档。
    """
    temp_file = _write_ohlcv(tmp_path / "ohlcv.json")
    params = {"ema": {"lengths": [50, 200]}}

    res = await CalculateIndicatorsTool().execute(
        temp_file=str(temp_file), indicators=["ema"], params=params
    )
    assert res.success is True, f"计算失败：{res.error!r}"
    assert params == {"ema": {"lengths": [50, 200]}}, (
        f"调用方 params 被就地改写：{params}"
    )


def test_schema_offers_real_indicator_names_without_prior_execute():
    """schema 的 indicators enum 必须在**第一次执行之前**就是真实指标名。

    原实现 ``_get_available_indicators()`` 直接 ``df.ta.indicators()``，而
    ``DataFrame.ta`` 只在 ``import pandas_ta`` 之后才存在。schema 先于 execute
    构建，于是 AttributeError 被吞掉、返回 []，enum 退化成只剩 legacy 的
    ``['bollinger']``——模型被告知只有 bollinger 可用。

    用子进程保证「本进程尚未 import pandas_ta」这个真实前提。
    """
    code = (
        "import json;"
        "from apps.agent.tools.market_data import CalculateIndicatorsTool;"
        "e=CalculateIndicatorsTool().parameters_schema['properties']['indicators']"
        "['items']['enum'];"
        "print(json.dumps({'n': len(e), 'has_ema': 'ema' in e, 'sample': e[:5]}))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).resolve().parents[3]),
        timeout=120,
    )
    assert out.returncode == 0, f"子进程失败：{out.stderr[-2000:]}"
    payload = json.loads(out.stdout.strip().splitlines()[-1])
    assert payload["n"] > 10, f"enum 只有 {payload['n']} 项，指标列表没取到：{payload}"
    assert payload["has_ema"] is True, f"enum 缺少 ema：{payload['sample']}"
