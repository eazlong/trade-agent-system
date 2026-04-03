#!/usr/bin/env python3
"""
代码变更验证技能执行脚本

在修改 Python 文件后自动运行格式化、Lint、类型检查和测试。
用于 Claude Code hooks 或 CI/CD 流程。

使用方法:
    python .agents/skills/code-change-verification/run.py [--files file1.py file2.py]
"""

import subprocess
import sys
import os
from pathlib import Path
from typing import List, Tuple


def run_command(cmd: List[str], cwd: str = "backend") -> Tuple[bool, str]:
    """运行命令并返回结果"""
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=300,
        )
        return result.returncode == 0, result.stdout + result.stderr
    except subprocess.TimeoutExpired:
        return False, "命令超时"
    except Exception as e:
        return False, str(e)


def check_format() -> Tuple[bool, str]:
    """检查代码格式"""
    return run_command(["ruff", "format", "--check", "apps/"])


def check_lint() -> Tuple[bool, str]:
    """检查 Lint"""
    return run_command(["ruff", "check", "apps/"])


def check_types() -> Tuple[bool, str]:
    """类型检查"""
    return run_command(["pyright", "apps/"])


def check_tests() -> Tuple[bool, str]:
    """运行测试"""
    env = os.environ.copy()
    env["DJANGO_SETTINGS_MODULE"] = "core.settings.dev"
    try:
        result = subprocess.run(
            ["pytest", "apps/", "--cov=apps", "--cov-report=term-missing", "-q"],
            cwd="backend",
            env=env,
            capture_output=True,
            text=True,
            timeout=600,
        )
        return result.returncode == 0, result.stdout + result.stderr
    except Exception as e:
        return False, str(e)


def check_invariants() -> Tuple[bool, str]:
    """不变量检查"""
    env = os.environ.copy()
    env["DJANGO_SETTINGS_MODULE"] = "core.settings.dev"
    try:
        result = subprocess.run(
            ["python", "scripts/check_invariants.py"],
            cwd="backend",
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        return result.returncode == 0, result.stdout + result.stderr
    except Exception as e:
        return False, str(e)


def main():
    """运行所有验证步骤"""
    print("=" * 60)
    print("🔍 代码变更验证")
    print("=" * 60)

    results = []

    # 1. 格式化检查
    print("\n📋 1/5 格式化检查...")
    passed, output = check_format()
    results.append(("格式化", passed, output))
    print("✅ 通过" if passed else "❌ 失败")

    # 2. Lint 检查
    print("\n📋 2/5 Lint 检查...")
    passed, output = check_lint()
    results.append(("Lint", passed, output))
    print("✅ 通过" if passed else "❌ 失败")

    # 3. 类型检查
    print("\n📋 3/5 类型检查...")
    passed, output = check_types()
    results.append(("类型检查", passed, output))
    print("✅ 通过" if passed else "❌ 失败")

    # 4. 测试
    print("\n📋 4/5 单元测试...")
    passed, output = check_tests()
    results.append(("测试", passed, output))
    print("✅ 通过" if passed else "❌ 失败")

    # 5. 不变量检查
    print("\n📋 5/5 不变量检查...")
    passed, output = check_invariants()
    results.append(("不变量", passed, output))
    print("✅ 通过" if passed else "❌ 失败")

    # 汇总结果
    print("\n" + "=" * 60)
    print("📊 验证结果汇总")
    print("=" * 60)

    all_passed = True
    for name, passed, output in results:
        status = "✅" if passed else "❌"
        print(f"{status} {name}")
        if not passed:
            all_passed = False
            print(f"   错误详情:\n{output[:500]}...")

    if all_passed:
        print("\n🎉 所有验证通过！")
        sys.exit(0)
    else:
        print("\n⚠️ 验证失败，请修复后重试")
        sys.exit(1)


if __name__ == "__main__":
    main()