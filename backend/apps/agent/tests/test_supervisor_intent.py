"""Tests for dynamic intent registration in supervisor."""
import pytest

from apps.agent.supervisor import (
    IntentRouter,
    register_intent,
    register_frame_intent,
    register_fallback_rule,
)


@pytest.fixture(autouse=True)
def reset_router():
    """Reset IntentRouter singleton between tests."""
    IntentRouter.reset()
    yield
    IntentRouter.reset()


class TestIntentRouter:
    """Test IntentRouter registration and lookup."""

    def test_register_and_lookup_intent(self):
        router = IntentRouter.get_instance()
        router.register_intent('test_intent', 'test_agent')
        assert router.get_agent_for_intent('test_intent') == 'test_agent'
        assert router.get_intent_for_agent('test_agent') == 'test_intent'

    def test_unregister_intent(self):
        router = IntentRouter.get_instance()
        router.register_intent('temp', 'temp_agent')
        router.unregister_intent('temp')
        assert router.get_agent_for_intent('temp') is None
        assert router.get_intent_for_agent('temp_agent') is None

    def test_register_intents_batch(self):
        router = IntentRouter.get_instance()
        router.register_intents({'a': 'agent_a', 'b': 'agent_b'})
        assert router.get_agent_for_intent('a') == 'agent_a'
        assert router.get_agent_for_intent('b') == 'agent_b'

    def test_frame_intent_registration(self):
        router = IntentRouter.get_instance()
        router.register_frame_intent('do_thing', 'frame_x', 'start')
        assert router.is_frame_intent('do_thing') is True
        assert router.get_frame_intent('do_thing') == ('frame_x', 'start')
        assert router.is_frame_intent('nonexistent') is False

    def test_fallback_rule_matching(self):
        router = IntentRouter.get_instance()
        router.register_fallback_rule(r'(测试|hello)', 'test_intent')
        assert router.match_fallback('这是一个测试') == 'test_intent'
        assert router.match_fallback('hello world') == 'test_intent'
        assert router.match_fallback('no match here') is None

    def test_valid_intents_for_prompt(self):
        router = IntentRouter.get_instance()
        router.register_intent('i1', 'a1')
        router.register_frame_intent('f1', 'frame', 'start')
        intents = router.valid_intents_for_prompt()
        assert 'i1' in intents
        assert 'f1' in intents

    def test_all_intents_empty_initially(self):
        router = IntentRouter.get_instance()
        assert router.all_intents() == []
        assert router.all_frame_intents() == []


class TestDecorators:
    """Test intent registration decorators."""

    def test_register_intent_decorator(self):
        @register_intent('decorated_intent', 'decorated_agent')
        class DummyAgent:
            pass

        router = IntentRouter.get_instance()
        assert router.get_agent_for_intent('decorated_intent') == 'decorated_agent'

    def test_register_frame_intent_decorator(self):
        @register_frame_intent('decorated_frame', 'my_frame', 'toggle')
        def dummy_func():
            pass

        router = IntentRouter.get_instance()
        assert router.get_frame_intent('decorated_frame') == ('my_frame', 'toggle')

    def test_register_fallback_rule_decorator(self):
        @register_fallback_rule(r'decorated.*pattern', 'decorated_intent')
        class AnotherDummy:
            pass

        router = IntentRouter.get_instance()
        assert router.match_fallback('decorated text pattern') == 'decorated_intent'


class TestBackwardCompat:
    """Test that default built-in intents are registered."""

    def _register_defaults(self, router):
        """重新注册模块级默认意图（autouse fixture 重置后需要）。"""
        router.register_intents({
            'analyze_market': 'analyst',
            'generate_signal': 'analyst',
            'generate_strategy': 'quant',
            'run_backtest': 'backtest',
            'create_plan': 'coach',
            'create_trading_system': 'coach',
            'review_trade': 'coach',
            'summarize_week': 'coach',
            'assess_risk': 'risk_advisor',
        })
        router.register_frame_intent('start_trading', 'trading', 'start')
        router.register_frame_intent('stop_trading', 'trading', 'stop')
        router.register_frame_intent('start_monitor', 'assist', 'start')
        router.register_frame_intent('stop_monitor', 'assist', 'stop')
        router.register_fallback_rules([
            (r'(分析|行情|走势|K线|趋势).*(BTC|ETH|币|市场)', 'analyze_market'),
            (r'(回测|测试策略|历史数据)', 'run_backtest'),
            (r'(风险|止损|仓位|风控)', 'assess_risk'),
            (r'(计划|复盘|总结|周报)', 'create_plan'),
            (r'(策略|代码|编写)', 'generate_strategy'),
        ])

    def test_default_intents_exist(self):
        router = IntentRouter.get_instance()
        self._register_defaults(router)
        assert router.get_agent_for_intent('analyze_market') == 'analyst'
        assert router.get_agent_for_intent('generate_strategy') == 'quant'
        assert router.get_agent_for_intent('assess_risk') == 'risk_advisor'

    def test_default_frame_intents_exist(self):
        router = IntentRouter.get_instance()
        self._register_defaults(router)
        assert router.get_frame_intent('start_trading') == ('trading', 'start')
        assert router.get_frame_intent('stop_monitor') == ('assist', 'stop')

    def test_default_fallback_rules_work(self):
        router = IntentRouter.get_instance()
        self._register_defaults(router)
        assert router.match_fallback('分析BTC行情') == 'analyze_market'
        assert router.match_fallback('回测策略') == 'run_backtest'
        assert router.match_fallback('风控建议') == 'assess_risk'
