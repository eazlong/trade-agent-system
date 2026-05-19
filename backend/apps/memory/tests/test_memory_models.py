from django.apps import apps


class TestAgentMemoryFields:
    def test_user_id_field_exists(self):
        """VC-004: AgentMemory 包含 user_id 字段"""
        model = apps.get_model("agent", "AgentMemory")
        field_names = [f.name for f in model._meta.get_fields()]
        assert "user_id" in field_names

    def test_user_id_indexed(self):
        """VC-004: user_id 有 db_index"""
        model = apps.get_model("agent", "AgentMemory")
        field = model._meta.get_field("user_id")
        assert field.db_index is True

    def test_embedding_field_exists(self):
        """VC-004: AgentMemory 包含 embedding 字段，可为 NULL"""
        model = apps.get_model("agent", "AgentMemory")
        field_names = [f.name for f in model._meta.get_fields()]
        assert "embedding" in field_names

    def test_agent_name_field_exists(self):
        """VC-004: AgentMemory 包含 agent_name 字段"""
        model = apps.get_model("agent", "AgentMemory")
        field_names = [f.name for f in model._meta.get_fields()]
        assert "agent_name" in field_names
