"""回归测试：_on_kline_data 长跑回调必须 close_old_connections。

Bug：K 线回调走 sync_to_async 线程池，pgbouncer/PG 空闲关闭连接后，下一次
ORM 操作抛 "connection already closed"，整个信号检查静默失败。
修复：在 run_check 进入时先调 close_old_connections() 刷新连接。
"""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest
from django.db import close_old_connections


@pytest.mark.asyncio
async def test_on_kline_data_refreshes_db_connection():
    """K 线回调进入 run_check 时必须调用 close_old_connections()。"""
    from apps.agent.frame_manager import FrameManager

    fm = FrameManager.__new__(FrameManager)  # 跳过 __init__ 的 Redis 访问
    fm._kline_check_sem = asyncio.Semaphore(2)  # 必须初始化为真 Semaphore

    with patch("apps.agent.frame_manager.close_old_connections") as mock_close:
        engine_mock = MagicMock()
        engine_mock._load_klines_for_monitors.return_value = {}
        engine_mock.check_signals_for_kline = MagicMock()

        with patch(
            "apps.signal_monitor.engine.SignalMonitorEngine.get_instance",
            return_value=engine_mock,
        ):
            await fm._on_kline_data({"close": 1.0}, "BTC/USDT")

    assert mock_close.called, (
        "close_old_connections() 必须在 run_check 内部调用，"
        "否则长跑回调的 DB 连接会被 pgbouncer 关闭导致 'connection already closed'"
    )
