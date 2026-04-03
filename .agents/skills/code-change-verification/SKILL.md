---
name: code-change-verification
description: 代码变更验证技能，在修改运行时代码后强制运行格式化、Lint、类型检查和测试堆栈
when_to_use: 当修改 backend/apps/ 下的任何 Python 文件时自动激活
references:
  - .agents/references/test_standards.md
---

# 代码变更验证流程

## 验证步骤（必须全部通过）

### 1. 格式化检查
```bash
cd backend
ruff format apps/
```

### 2. Lint 检查
```bash
ruff check apps/
```

### 3. 类型检查
```bash
pyright apps/
```

### 4. 单元测试
```bash
DJANGO_SETTINGS_MODULE=core.settings.dev pytest apps/ --cov=apps --cov-report=term-missing
```

### 5. 不变量检查
```bash
DJANGO_SETTINGS_MODULE=core.settings.dev python scripts/check_invariants.py
```

## 成功标准 (Done Criteria)

- ✅ Ruff format 无变更
- ✅ Ruff check 无错误
- ✅ Pyright 无错误
- ✅ pytest 全部通过
- ✅ 覆盖率 ≥ 80%
- ✅ 不变量检查全部通过

## 失败处理

验证失败时：
1. 捕获完整 Traceback
2. 分析错误原因
3. 执行自主修复
4. 重新运行验证流程

## 注意事项

- **禁止跳过**: 所有步骤必须通过
- **禁止修改测试**: 测试失败时应修改实现代码，而非测试代码
- **禁止 console.log**: 提交前必须移除所有调试输出