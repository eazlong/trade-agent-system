分析并修复 GitHub issue: $ARGUMENTS

1. 用 `gh issue view $ARGUMENTS` 获取 issue 详情
2. 搜索相关代码，理解问题根因
3. 编写失败测试复现问题
4. 实现最小修复使测试通过
5. 运行完整测试套件确保无回归
6. 提交修复并创建 PR，PR 描述中引用 issue
