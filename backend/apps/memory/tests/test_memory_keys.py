from apps.memory.keys import MemoryKey


class TestMemoryKey:
    def test_conv_history_format(self):
        """VC-002: 统一 Key 格式为 mem:conv:{user_id}"""
        key = MemoryKey.conv_history("user1")
        assert key == "mem:conv:user1"

    def test_l2_private_format(self):
        """VC-002: 私有区 key 格式"""
        key = MemoryKey.l2_private("analyst", "user1")
        assert key == "mem:l2:analyst:user1"

    def test_l2_shared_format(self):
        """VC-002: 共享区 key 格式"""
        key = MemoryKey.l2_shared("user1")
        assert key == "mem:l2:shared:user1"

    def test_l2_session_format(self):
        """VC-002: 会话级 key 格式"""
        key = MemoryKey.l2_session("user1", "sess123")
        assert key == "mem:l2:session:user1:sess123"
