#!/usr/bin/env python
"""
复现 task_tracker.py 在 PostgreSQL 重启后遇到的连接问题

这个脚本模拟 Celery worker 在无事件循环的环境下执行数据库操作时，
遇到 OperationalError 的场景。
"""
import os
import sys
import django

# 设置 Django 环境
sys.path.insert(0, '/Users/gongzuoyonghu/Documents/code/blockchain/bot/qt_sys/trade_agent_sys/backend')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings.dev')

# 强制使用 Docker 环境
os.environ['DB_HOST'] = 'pgbouncer'
os.environ['DB_PORT'] = '5432'
os.environ['DB_NAME'] = 'trade_agent'
os.environ['DB_USER'] = 'trade'
os.environ['DB_PASSWORD'] = 'trade'

django.setup()

from apps.agent.task_tracker import TaskTracker
from apps.agent.models import TaskProgress
from django.db import connection
import uuid


def test_connection_recovery():
    """测试数据库连接恢复"""
    print("=" * 60)
    print("测试 1: 正常数据库操作")
    print("=" * 60)

    task_id = str(uuid.uuid4())

    try:
        # 创建 TaskTracker 实例
        tracker = TaskTracker(task_id=task_id, user_id="test-user-123")

        # 模拟任务完成，触发归档
        print(f"尝试归档任务: {task_id}")
        tracker.complete("测试完成")

        # 验证是否成功写入数据库
        progress = TaskProgress.objects.get(task_id=task_id)
        print(f"✓ 成功写入数据库: status={progress.status}")

        # 清理
        progress.delete()
        print("✓ 测试通过")
        return True

    except Exception as e:
        print(f"✗ 测试失败: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_stale_connection():
    """模拟陈旧连接场景"""
    print("\n" + "=" * 60)
    print("测试 2: 模拟陈旧连接")
    print("=" * 60)

    # 手动关闭底层连接，模拟 PostgreSQL 重启
    print("手动关闭数据库连接...")
    connection.close()

    task_id = str(uuid.uuid4())

    try:
        # 创建 TaskTracker 实例
        tracker = TaskTracker(task_id=task_id, user_id="test-user-456")

        # 尝试使用已关闭的连接
        print(f"尝试使用已关闭的连接归档任务: {task_id}")
        tracker.complete("测试完成")

        # 验证是否成功写入数据库
        progress = TaskProgress.objects.get(task_id=task_id)
        print(f"✓ 成功恢复并写入数据库: status={progress.status}")

        # 清理
        progress.delete()
        print("✓ 连接恢复测试通过")
        return True

    except Exception as e:
        print(f"✗ 连接恢复测试失败: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()

        # 尝试恢复连接供后续测试
        print("\n尝试恢复连接...")
        connection.ensure_connection()
        if connection.is_usable():
            print("✓ 连接已恢复")
        else:
            print("✗ 连接仍然不可用")

        return False


if __name__ == "__main__":
    print("开始测试数据库连接恢复机制\n")

    test1_passed = test_connection_recovery()
    test2_passed = test_stale_connection()

    print("\n" + "=" * 60)
    print("测试总结")
    print("=" * 60)
    print(f"测试 1 (正常操作): {'✓ 通过' if test1_passed else '✗ 失败'}")
    print(f"测试 2 (连接恢复): {'✓ 通过' if test2_passed else '✗ 失败'}")

    if test1_passed and test2_passed:
        print("\n✓ 所有测试通过")
        sys.exit(0)
    else:
        print("\n✗ 部分测试失败")
        sys.exit(1)
