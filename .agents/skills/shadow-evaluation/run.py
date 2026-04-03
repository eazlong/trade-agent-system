#!/usr/bin/env python3
"""
影子评估技能执行脚本

使用影子评估器验证核心业务逻辑正确性。

使用方法:
    python .agents/skills/shadow-evaluation/run.py --function order_matching --input test_data.json
"""

import sys
import os
import json
import argparse

# 添加项目路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "backend"))

from utils.testing.shadow_evaluator import get_evaluator, ShadowEvaluator


def main():
    parser = argparse.ArgumentParser(description="影子评估")
    parser.add_argument("--function", type=str, required=True, help="评估函数名")
    parser.add_argument("--input", type=str, help="输入数据 JSON 文件")
    parser.add_argument("--threshold", type=float, default=0.001, help="误差阈值")
    args = parser.parse_args()

    # 加载输入数据
    if args.input:
        with open(args.input, "r") as f:
            input_data = json.load(f)
    else:
        input_data = {}

    # 示例输出（实际使用时应传入真实输出）
    actual_output = {}

    print("=" * 60)
    print(f"🔮 影子评估: {args.function}")
    print("=" * 60)

    evaluator = get_evaluator(error_threshold=args.threshold)

    # 检查参考实现是否注册
    if args.function not in evaluator._reference_implementations:
        print(f"❌ 未找到参考实现: {args.function}")
        print(f"可用参考实现: {list(evaluator._reference_implementations.keys())}")
        sys.exit(1)

    print(f"📋 输入数据: {json.dumps(input_data, indent=2)[:200]}...")
    print(f"📊 误差阈值: {args.threshold}")

    # 注意：这里需要传入实际的输出进行评估
    print("\n⚠️ 请传入实际输出进行评估:")
    print(f"   result = evaluator.evaluate('{args.function}', actual_output, input_data)")

    print("\n✅ 影子评估器已就绪")


if __name__ == "__main__":
    main()