"""
BUGGIFY - 合成故障注入模块

用于确定性仿真测试（DST），在关键代码路径中注入合成故障，
人为扩大并发干扰窗口，捕获隐蔽的时序、并发、网络漏洞。

使用方法:
    from utils.testing.buggify import buggify, BuggifyContext

    @shared_task
    def process_order(order_id):
        order = Order.objects.get(id=order_id)

        # 故障注入点：模拟读取订单和锁定资金之间的延迟
        if buggify():
            pass

        lock_funds(order.user)  # DST 将验证是否出现重复扣款

DST 测试:
    SIMULATION_MODE=True pytest apps/trading/tests/dst/
"""

import random
import time
import os
from contextlib import contextmanager
from typing import Optional, Callable, Any
import functools


class BuggifyConfig:
    """BUGGIFY 配置"""

    # 是否启用仿真模式
    SIMULATION_MODE: bool = os.getenv("SIMULATION_MODE", "False") == "True"

    # 故障触发概率（默认 25%）
    TRIGGER_PROBABILITY: float = float(os.getenv("BUGGIFY_PROBABILITY", "0.25"))

    # 延迟范围（秒）
    DELAY_MIN: float = float(os.getenv("BUGGIFY_DELAY_MIN", "0.01"))
    DELAY_MAX: float = float(os.getenv("BUGGIFY_DELAY_MAX", "0.1"))

    # 确定性种子（用于可复现测试）
    _seed: Optional[int] = None

    @classmethod
    def set_seed(cls, seed: int) -> None:
        """设置确定性种子，使故障注入可复现"""
        cls._seed = seed
        random.seed(seed)

    @classmethod
    def reset_seed(cls) -> None:
        """重置种子"""
        cls._seed = None


def buggify() -> bool:
    """
    合成故障注入：在生产代码中预留的故障触发点

    仅在 DST 仿真环境且命中随机概率时触发故障：
    - 注入微秒级延迟，模拟竞态条件
    - 可通过确定性种子使失败可复现

    Returns:
        bool: True 表示故障被触发，False 表示正常执行
    """
    if not BuggifyConfig.SIMULATION_MODE:
        return False

    if random.random() < BuggifyConfig.TRIGGER_PROBABILITY:
        # 注入随机延迟，扩大并发窗口
        delay = random.uniform(BuggifyConfig.DELAY_MIN, BuggifyConfig.DELAY_MAX)
        time.sleep(delay)
        return True

    return False


def buggify_delay(custom_min: Optional[float] = None, custom_max: Optional[float] = None) -> bool:
    """
    自定义延迟范围的故障注入

    Args:
        custom_min: 自定义最小延迟（秒）
        custom_max: 自定义最大延迟（秒）

    Returns:
        bool: True 表示故障被触发
    """
    if not BuggifyConfig.SIMULATION_MODE:
        return False

    min_delay = custom_min or BuggifyConfig.DELAY_MIN
    max_delay = custom_max or BuggifyConfig.DELAY_MAX

    if random.random() < BuggifyConfig.TRIGGER_PROBABILITY:
        delay = random.uniform(min_delay, max_delay)
        time.sleep(delay)
        return True

    return False


class BuggifyContext:
    """
    BUGGIFY 上下文管理器，用于在特定代码块中强制故障

    使用方法:
        with BuggifyContext.force_fault():
            # 此代码块必定触发故障
            critical_operation()
    """

    def __init__(self, force: bool = False, fault_type: str = "delay"):
        self.force = force
        self.fault_type = fault_type

    @classmethod
    @contextmanager
    def force_fault(cls, fault_type: str = "delay"):
        """强制触发故障的上下文管理器"""
        ctx = cls(force=True, fault_type=fault_type)
        try:
            if BuggifyConfig.SIMULATION_MODE:
                delay = random.uniform(BuggifyConfig.DELAY_MIN, BuggifyConfig.DELAY_MAX)
                time.sleep(delay)
            yield ctx
        finally:
            pass

    @classmethod
    @contextmanager
    def network_partition(cls, duration: float = 0.5):
        """模拟网络分区"""
        ctx = cls(force=True, fault_type="network_partition")
        try:
            if BuggifyConfig.SIMULATION_MODE:
                time.sleep(duration)
            yield ctx
        finally:
            pass

    @classmethod
    @contextmanager
    def disk_latency(cls, duration: float = 0.1):
        """模拟磁盘延迟"""
        ctx = cls(force=True, fault_type="disk_latency")
        try:
            if BuggifyConfig.SIMULATION_MODE:
                time.sleep(duration)
            yield ctx
        finally:
            pass


def buggify_decorator(
    probability: float = 0.5,
    delay_min: float = 0.01,
    delay_max: float = 0.1
) -> Callable:
    """
    BUGGIFY 装饰器，用于自动在函数执行前后注入故障

    使用方法:
        @buggify_decorator(probability=0.3)
        def my_function():
            pass

    Args:
        probability: 故障触发概率
        delay_min: 最小延迟
        delay_max: 最大延迟
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            if BuggifyConfig.SIMULATION_MODE and random.random() < probability:
                delay = random.uniform(delay_min, delay_max)
                time.sleep(delay)

            result = func(*args, **kwargs)

            if BuggifyConfig.SIMULATION_MODE and random.random() < probability:
                delay = random.uniform(delay_min, delay_max)
                time.sleep(delay)

            return result

        return wrapper

    return decorator


# 预定义的故障类型
FAULT_TYPES = {
    "delay": lambda: time.sleep(random.uniform(0.01, 0.1)),
    "network_partition": lambda: time.sleep(0.5),
    "disk_latency": lambda: time.sleep(0.1),
    "memory_pressure": lambda: None,  # 占位符，需要具体实现
    "redis_timeout": lambda: time.sleep(0.3),
    "db_deadlock": lambda: time.sleep(0.2),
}


def inject_fault(fault_type: str = "delay") -> bool:
    """
    注入特定类型的故障

    Args:
        fault_type: 故障类型（delay, network_partition, disk_latency 等）

    Returns:
        bool: True 表示故障被注入
    """
    if not BuggifyConfig.SIMULATION_MODE:
        return False

    if random.random() < BuggifyConfig.TRIGGER_PROBABILITY:
        fault_func = FAULT_TYPES.get(fault_type)
        if fault_func:
            fault_func()
            return True

    return False