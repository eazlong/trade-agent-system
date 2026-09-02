#!/bin/bash
# PgBouncer 回滚脚本
# 用途：如遇问题，快速回滚到直连 PostgreSQL

echo "=== 回滚 PgBouncer 配置 ==="

# 1. 停止 pgbouncer 容器
docker compose stop pgbouncer

# 2. 修改 docker-compose.yml（恢复直连）
sed -i.bak 's/DB_HOST=pgbouncer/DB_HOST=db/g' docker-compose.yml
sed -i.bak 's/DB_PORT=6432/DB_PORT=5432/g' docker-compose.yml
sed -i.bak 's/depends_on:\n      - pgbouncer/depends_on:\n      db:\n        condition: service_healthy/g' docker-compose.yml

# 3. 重启服务
docker compose restart backend celery lark-ws celery-beat

# 4. 验证连接
docker compose exec backend python -c "
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings.dev')
import django
django.setup()
from django.db import connection
cursor = connection.cursor()
cursor.execute('SELECT 1')
print('✅ PostgreSQL 直连成功')
"

echo "=== 回滚完成 ==="
