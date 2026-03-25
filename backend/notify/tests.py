from django.test import TestCase
from qtcore.notifier import Notify
from strategy.models.strategy import TbStrategyTemplate
from django.contrib.auth import get_user_model
from qtcore.strategy import StrategySignal, Direct
User = get_user_model()


class NotifyTestCase(TestCase):
    def setUp(self):
        user = User.objects.get(id=1)
        print(user)
        self.t = TbStrategyTemplate.objects.get(id=2)
        self.s = Notify(user)
   
    def test_analyse_data(self):
        self.s.do(StrategySignal(Direct.LONG, {}, "BTCUSDT", "LONG", {}, "15m"))
  

