#!/usr/bin/env python3
"""
DST 确定性仿真测试技能执行脚本

使用 BUGGIFY 故障注入运行确定性仿真测试。

使用方法:
    python .agents/skills/dst-testing/run.py [--seeds 100] [--module trading]
"""

import subprocess
import sys
import os
import argparse
from typing import List, Tuple


def run_dst_test(seeds: int, module: str = "trading") -> Tuple[bool, str]:
    """运行 DST 测试"""
    env = os.environ.copy()
    env["SIMULATION_MODE"] = "True"
    env["DJANGO_SETTINGS_MODULE"] = "core.settings.dev"

    test_path = f"apps/{module}/tests/dst/" if module else "apps/"

    try:
        result = subprocess.run(
            ["pytest", test_path, "-v", "-q", f"--count={seeds}"],
            cwd="backend",
            env=env,
            capture_output=True,
            text=True,
            timeout=600,
        )
        return result.returncode == 0, result.stdout + result.stderr
    except Exception as e:
        return False, str(e)


def main():
    parser = argparse.ArgumentParser(description="DST 确定性仿真测试")
    parser.add_argument("--seeds", type=int, default=100, help="种子数量")
    parser.add_argument("--module", type=str, default="trading", help="测试模块")
    args = parser.parse_args()

    print("=" * 60)
    print(f"🔬 DST 确定性仿真测试 ({args.seeds} 种子)")
    print("=" * 60)

    passed, output = run_dst_test(args.seeds, args.module)

    print(output)

    if passed:
        print("\n🎉 DST 测试通过！")
        sys.exit(0)
    else:
        print("\n⚠️ DST 测试失败")
        sys.exit(1)


if __name__ == "__main__":
    main()