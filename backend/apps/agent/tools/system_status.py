"""System status tool — get global system status for SupervisorAgent."""

from __future__ import annotations

import logging
from datetime import datetime

from .base import BaseTool, ToolResult

logger = logging.getLogger(__name__)


class GetSystemStatusTool(BaseTool):
    """
    获取系统全局状态，包括 Agent 状态、数据源状态、交易系统状态、
    交易监控状态、技能清单等。

    仅供 SupervisorAgent 使用，响应用户的系统状态查询。
    """

    name = "get_system_status"
    description = (
        "获取系统全局状态概览。返回当前所有 Agent 状态、数据源状态、"
        "交易系统状态（交易/辅助框架、风控、订单执行器）、"
        "技能清单、工具清单、监控信号等信息。"
    )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "section": {
                    "type": "string",
                    "description": "可选，只返回指定部分的状态。可选值：all, agents, frames, data_quality, skills, tools, signals, risk",
                    "enum": [
                        "all",
                        "agents",
                        "frames",
                        "data_quality",
                        "skills",
                        "tools",
                        "signals",
                        "risk",
                    ],
                    "default": "all",
                },
            },
            "required": [],
        }

    async def execute(self, section: str = "all", **kwargs) -> ToolResult:
        try:
            result = {}

            if section in ("all", "agents"):
                result["agents"] = self._get_agent_status()

            if section in ("all", "frames"):
                result["frames"] = await self._get_frame_status()

            if section in ("all", "data_quality"):
                result["data_quality"] = self._get_data_quality_status()

            if section in ("all", "skills"):
                result["skills"] = self._get_skills_status()

            if section in ("all", "tools"):
                result["tools"] = self._get_tools_status()

            if section in ("all", "signals"):
                result["signals"] = self._get_signal_monitor_status()

            if section in ("all", "risk"):
                result["risk"] = self._get_risk_status()

            result["timestamp"] = datetime.now().isoformat()

            return ToolResult(success=True, data=result)

        except Exception as e:
            logger.error("[GetSystemStatusTool] error: %s", e)
            return ToolResult(success=False, error=f"获取系统状态失败: {e}")

    # ------------------------------------------------------------------ #
    #  Agent 状态                                                         #
    # ------------------------------------------------------------------

    def _get_agent_status(self) -> dict:
        from apps.agent.registry import AgentRegistry
        from apps.agent.supervisor import IntentRouter

        AgentRegistry.discover_from_prompts()

        agents = []
        for name in sorted(AgentRegistry.all_names()):
            instantiated = name in AgentRegistry._registry
            intent = IntentRouter.get_instance().get_intent_for_agent(name)
            agents.append(
                {
                    "name": name,
                    "instantiated": instantiated,
                    "intent": intent or "N/A",
                }
            )

        # Supervisor 状态
        from apps.agent.supervisor import SupervisorAgent
        from apps.skill.loader import get_skills_loader

        supervisor = SupervisorAgent._instance
        loader = get_skills_loader("supervisor") if supervisor else None
        sup_status = (
            {
                "name": "supervisor",
                "running": supervisor is not None,
                "skills_loaded": len(loader.get_always_skills()) if loader else 0,
            }
            if supervisor
            else {"name": "supervisor", "running": False, "skills_loaded": 0}
        )

        # 意图路由
        router = IntentRouter.get_instance()
        intents = {
            "agent_intents": router.all_intents(),
            "frame_intents": router.all_frame_intents(),
            "fallback_rules_count": len(router._fallback_rules),
        }

        return {
            "supervisor": sup_status,
            "sub_agents": agents,
            "total_agents": len(agents) + 1,
            "intent_routing": intents,
        }

    # ------------------------------------------------------------------ #
    #  交易/辅助框架状态                                                    #
    # ------------------------------------------------------------------

    async def _get_frame_status(self) -> dict:
        from apps.agent.frame_manager import FrameManager

        frame = FrameManager.get_instance()
        status = frame.status()

        return {
            "trading_frame": status.get("trading", "stopped"),
            "assist_frame": status.get("assist", "stopped"),
            "risk_guard": status.get("risk_guard", "stopped"),
            "order_executor": status.get("order_executor", "stopped"),
        }

    # ------------------------------------------------------------------ #
    #  数据质量状态                                                         #
    # ------------------------------------------------------------------

    def _get_data_quality_status(self) -> dict:
        try:
            from apps.datasource.monitor import get_quality_monitor

            monitor = get_quality_monitor()
            stats = monitor.get_stats()

            return {
                "monitoring": True,
                "total_reports": stats["total_reports"],
                "status_counts": stats["status_counts"],
                "avg_latencies": {
                    k: round(v, 1) for k, v in stats["avg_latencies"].items()
                },
                "thresholds": {
                    "latency_ms": stats["latency_threshold"],
                    "completeness": stats["completeness_threshold"],
                    "accuracy": stats["accuracy_threshold"],
                },
            }
        except Exception as e:
            return {
                "monitoring": False,
                "error": str(e),
            }

    # ------------------------------------------------------------------ #
    #  技能状态                                                             #
    # ------------------------------------------------------------------

    def _get_skills_status(self) -> dict:
        from apps.skill.loader import get_skills_loader

        loader = get_skills_loader("supervisor")
        all_skills = loader.list_skills()
        always_skills = loader.get_always_skills()

        return {
            "total_skills": len(all_skills),
            "always_skills": always_skills,
            "available_skills": [
                {
                    "name": s["name"],
                    "description": s["description"],
                    "source": s["source"],
                }
                for s in all_skills
            ],
        }

    # ------------------------------------------------------------------ #
    #  工具状态                                                             #
    # ------------------------------------------------------------------

    def _get_tools_status(self) -> dict:
        from apps.agent.tools.base import ToolRegistry

        tools = [
            {"name": t.name, "description": t.description} for t in ToolRegistry.all()
        ]

        return {
            "total_tools": len(tools),
            "tools": tools,
        }

    # ------------------------------------------------------------------ #
    #  信号监控状态                                                         #
    # ------------------------------------------------------------------

    def _get_signal_monitor_status(self) -> dict:
        """获取当前监控的交易信号和数据流状态"""
        from apps.agent.frame_manager import FrameManager
        from apps.agent.supervisor import IntentRouter

        frame = FrameManager.get_instance()
        frame_status = frame.status()

        # 从 IntentRouter 获取监控相关的框架意图
        router = IntentRouter.get_instance()
        monitor_intents = [
            intent
            for intent in router.all_frame_intents()
            if "monitor" in intent.lower() or "trading" in intent.lower()
        ]

        # 监控信号清单（根据系统配置推断）
        monitored_signals = []

        # 如果辅助框架在运行，说明在监控信号
        if frame_status.get("assist") == "running":
            monitored_signals.append("signal_stream")
            monitored_signals.append("market_data_feed")

        # 如果交易框架在运行，说明在监控订单和持仓
        if frame_status.get("trading") == "running":
            monitored_signals.append("order_stream")
            monitored_signals.append("position_stream")
            monitored_signals.append("risk_events")

        return {
            "assist_running": frame_status.get("assist") == "running",
            "trading_running": frame_status.get("trading") == "running",
            "monitored_signals": monitored_signals,
            "signal_streams": {
                "agent:tasks": "Agent 任务队列",
                "trading:orders": "订单执行流",
                "trading:positions": "持仓同步流",
                "risk:events": "风险事件流",
            },
            "monitor_intents": monitor_intents,
        }

    # ------------------------------------------------------------------ #
    #  风控状态                                                             #
    # ------------------------------------------------------------------

    def _get_risk_status(self) -> dict:
        from apps.riskguard.guard import RiskGuard

        instance = RiskGuard.get_instance()
        if instance is None:
            return {
                "running": False,
                "mode": "N/A",
            }

        return {
            "running": instance._running,
            "mode": instance.mode,
            "thresholds": {
                "max_position_ratio": str(RiskGuard.MAX_POSITION_RATIO),
                "max_daily_drawdown": str(RiskGuard.MAX_DAILY_DRAWDOWN),
                "max_daily_trades": RiskGuard.MAX_DAILY_TRADES,
                "floating_loss_alert": str(RiskGuard.FLOATING_LOSS_ALERT),
            },
            "circuit_breaker": "per_user",
        }
