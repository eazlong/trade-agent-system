全面检查项目质量：

1. 后端检查：
   ```bash
   cd backend && uv run python manage.py check --deploy
   cd backend && uv run python manage.py test
   ```

2. 前端检查：
   ```bash
   cd frontend && npx next lint
   cd frontend && npm test
   cd frontend && yarn build
   ```

3. 汇总报告所有通过/失败项
