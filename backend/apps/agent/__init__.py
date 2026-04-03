# Auto-import sub_agents so AgentRegistry.register_class decorators run on startup
from apps.agent import sub_agents  # noqa: F401
