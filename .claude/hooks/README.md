# Claude Code Hooks 配置

本目录包含 Claude Code 的 hooks 配置，用于自动运行验证技能。

## Hooks 类型

### 1. PostToolUse Hook
在工具使用后自动触发验证：
- 编辑 Python 文件后运行代码变更验证
- 修改 trading 模块后运行 DST 测试

### 2. Stop Hook
在会话结束时运行完整验证：
- 确保所有变更都通过验证
- 检查是否有 console.log 调试语句

## 当前配置

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Edit",
        "hooks": [
          {
            "type": "command",
            "command": "python3 .agents/skills/code-change-verification/run.py"
          }
        ]
      }
    ],
    "Stop": [
      {
        "type": "command",
        "command": "python3 .agents/skills/code-change-verification/run.py"
      }
    ]
  }
}
```

## 验证流程

```
用户编辑代码 → PostToolUse Hook → 运行验证技能
                    ↓
              验证通过 → 继续
              验证失败 → 警告用户
```

## 手动触发验证

```bash
# 代码变更验证
python3 .agents/skills/code-change-verification/run.py

# DST 仿真测试
python3 .agents/skills/dst-testing/run.py --seeds 100

# 影子评估
python3 .agents/skills/shadow-evaluation/run.py --function order_matching
```

## CI/CD 集成

在 GitHub Actions 中强制执行：

```yaml
- name: Code Change Verification
  run: python3 .agents/skills/code-change-verification/run.py

- name: DST Testing
  run: python3 .agents/skills/dst-testing/run.py --seeds 1000
```