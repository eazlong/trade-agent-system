#!/bin/bash
# 启动脚本，支持不同的 ASGI 服务器

echo "启动交易代理系统..."

if [ "$1" = "uvicorn" ]; then
    echo "使用 Uvicorn 启动 (推荐)..."
    uvicorn core.asgi:application --host 0.0.0.0 --port 8000 --reload
elif [ "$1" = "daphne" ]; then
    echo "使用 Daphne 启动..."
    daphne -b 0.0.0.0 -p 8000 core.asgi:application
else
    echo "使用 Django runserver 启动 (部分功能受限)..."
    echo "注意: 使用 runserver 时 lifespan 事件可能不会触发"
    python manage.py runserver 0.0.0.0:8000
fi