# Auto-import all skill modules so SkillRegistry.register decorators run on startup
from apps.skill.skills import analysis_skill  # noqa: F401
from apps.skill.skills import strategy_skill  # noqa: F401
from apps.skill.skills import risk_skill      # noqa: F401
from apps.skill.skills import coach_skill     # noqa: F401
