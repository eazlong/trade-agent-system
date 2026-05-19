import pytest


@pytest.mark.xfail(reason="Migration 尚未运行")
class TestMigration0004:
    def test_migration_applies_cleanly(self):
        """Migration 0004 可成功应用"""
        pass

    def test_backfill_agent_name(self):
        """旧数据 agent_name = agent_type"""
        pass

    def test_backfill_user_id(self):
        """旧数据 user_id = 'migrated_unknown'"""
        pass
