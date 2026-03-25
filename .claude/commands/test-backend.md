运行后端测试：

```bash
cd backend && uv run python manage.py test $ARGUMENTS -v 2
```

如果测试失败，分析失败原因并修复。修复后重新运行确认通过。
