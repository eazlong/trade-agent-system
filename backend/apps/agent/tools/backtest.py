"""
回测工具 — 异步提交回测任务 & 查询任务状态。

设计原则：
  回测是长任务（几秒到几分钟），不能在 LLM 工具调用循环里同步等待。
  Agent 通过两步完成回测：
    1. submit_backtest → 返回 celery task_id（立即返回）
    2. get_task_result  → 轮询任务状态 / 获取结果
"""

from __future__ import annotations

import logging
from typing import Any

from .base import BaseTool, ToolResult

logger = logging.getLogger(__name__)


class SubmitBacktestTool(BaseTool):
    """
    提交回测任务到 Celery 队列，立即返回任务 ID。

    不阻塞等待结果，适合在 LLM Agent 工具循环中调用。
    结果通过 get_task_result 工具查询。
    """

    name = "submit_backtest"
    description = (
        "提交回测任务并立即返回任务ID。回测在后台异步运行，"
        "使用 get_task_result 查询任务状态和结果。"
    )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "strategy_name": {
                    "type": "string",
                    "description": "策略名称（StrategyRegistry 注册名）",
                },
                "symbol": {
                    "type": "string",
                    "description": "交易对，如 BTC/USDT 或 BTCUSDT",
                },
                "timeframe": {
                    "type": "string",
                    "description": "K线周期，如 1h、4h、1d",
                },
                "initial_capital": {
                    "type": "number",
                    "description": "初始资金（USDT），默认 10000",
                    "default": 10000,
                },
                "commission_rate": {
                    "type": "number",
                    "description": "手续费率，默认 0.001（0.1%）",
                    "default": 0.001,
                },
                "parameters": {
                    "type": "object",
                    "description": "策略自定义参数（可选）",
                },
                "strategy_id": {
                    "type": "string",
                    "description": "Strategy 模型 UUID，用于关联回测结果（可选）",
                },
                "exchange": {
                    "type": "string",
                    "description": "交易所名称，默认 binance",
                    "default": "binance",
                },
            },
            "required": ["strategy_name", "symbol", "timeframe"],
        }

    async def execute(self, **kwargs) -> ToolResult:
        strategy_name: str = kwargs.get("strategy_name", "")
        symbol: str = kwargs.get("symbol", "")
        timeframe: str = kwargs.get("timeframe", "")

        if not strategy_name or not symbol or not timeframe:
            return ToolResult(
                success=False,
                error="strategy_name、symbol、timeframe 均为必填项",
            )

        try:
            from apps.backtest.tasks import run_backtest_task

            task = run_backtest_task.apply_async(
                kwargs={
                    "strategy_name": strategy_name,
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "initial_capital": kwargs.get("initial_capital", 10000),
                    "commission_rate": kwargs.get("commission_rate", 0.001),
                    "parameters": kwargs.get("parameters"),
                    "strategy_id": kwargs.get("strategy_id"),
                    "exchange": kwargs.get("exchange", "binance"),
                }
            )
            logger.info(
                "[SubmitBacktestTool] submitted task_id=%s strategy=%s symbol=%s tf=%s",
                task.id,
                strategy_name,
                symbol,
                timeframe,
            )
            return ToolResult(
                success=True,
                data={
                    "task_id": task.id,
                    "status": "PENDING",
                    "message": (
                        f"回测任务已提交，task_id={task.id}。"
                        f"策略={strategy_name} 标的={symbol} 周期={timeframe}。"
                        "使用 get_task_result 查询进度和结果。"
                    ),
                },
            )
        except Exception as e:
            logger.error("[SubmitBacktestTool] submit failed: %s", e, exc_info=True)
            return ToolResult(success=False, error=f"提交回测任务失败: {e}")


class GetTaskResultTool(BaseTool):
    """
    查询 Celery 任务的状态和结果。

    支持回测等所有通过 Celery 异步提交的任务。
    状态值: PENDING / STARTED / SUCCESS / FAILURE / RETRY / REVOKED
    """

    name = "get_task_result"
    description = (
        "查询异步任务（如回测）的当前状态和结果。"
        "状态为 PENDING/STARTED 时表示任务仍在运行，"
        "SUCCESS 时返回完整结果，FAILURE 时返回错误信息。"
    )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "由 submit_backtest 返回的任务 ID",
                },
            },
            "required": ["task_id"],
        }

    async def execute(self, task_id: str = "", **kwargs) -> ToolResult:
        if not task_id:
            return ToolResult(success=False, error="task_id 为必填项")

        try:
            from celery_app import app as celery_app
            from celery.result import AsyncResult

            result: AsyncResult = celery_app.AsyncResult(task_id)
            state = result.state

            if state == "SUCCESS":
                return ToolResult(
                    success=True,
                    data={
                        "task_id": task_id,
                        "status": "SUCCESS",
                        "result": result.result,
                    },
                )
            elif state == "FAILURE":
                error_info = str(result.result) if result.result else "未知错误"
                return ToolResult(
                    success=True,  # 工具本身成功，只是任务失败
                    data={
                        "task_id": task_id,
                        "status": "FAILURE",
                        "error": error_info,
                    },
                )
            else:
                # PENDING / STARTED / RETRY / REVOKED
                info: Any = None
                if state == "STARTED" and result.info:
                    info = result.info  # 支持任务上报进度（如果有）
                return ToolResult(
                    success=True,
                    data={
                        "task_id": task_id,
                        "status": state,
                        "info": info,
                        "message": f"任务仍在运行中（{state}），请稍后再次查询。",
                    },
                )
        except Exception as e:
            logger.error("[GetTaskResultTool] query failed task_id=%s: %s", task_id, e)
            return ToolResult(success=False, error=f"查询任务状态失败: {e}")
