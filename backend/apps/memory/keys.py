"""Memory key constants — unified key format: mem:{scope}:{user_id}:{key_name}"""


class MemoryKey:
    """Unified memory key generator for L1/L2/L3."""

    PREFIX = "mem"

    @classmethod
    def conv_history(cls, user_id: str) -> str:
        """对话历史 key: mem:conv:{user_id}"""
        return f"{cls.PREFIX}:conv:{user_id}"

    @classmethod
    def l2_private(cls, agent_type: str, user_id: str) -> str:
        """L2 私有区 key: mem:l2:{agent_type}:{user_id}"""
        return f"{cls.PREFIX}:l2:{agent_type}:{user_id}"

    @classmethod
    def l2_shared(cls, user_id: str) -> str:
        """L2 共享区 key: mem:l2:shared:{user_id}"""
        return f"{cls.PREFIX}:l2:shared:{user_id}"

    @classmethod
    def l2_session(cls, user_id: str, session_id: str) -> str:
        """L2 会话级 key: mem:l2:session:{user_id}:{session_id}"""
        return f"{cls.PREFIX}:l2:session:{user_id}:{session_id}"
