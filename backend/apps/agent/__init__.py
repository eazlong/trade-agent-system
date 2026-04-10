# Auto-import sub_agents so AgentRegistry.register_class decorators run on startup
from apps.agent import sub_agents  # noqa: F401

# Dynamic intent registration
from apps.agent.supervisor import (  # noqa: F401
    IntentRouter,
    register_intent,
    register_frame_intent,
    register_fallback_rule,
)
