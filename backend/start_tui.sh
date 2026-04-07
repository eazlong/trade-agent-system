#!/bin/bash
# TradeAgent TUI 启动脚本
#
# 用法:
#   ./start_tui.sh           # 使用 .venv python
#   ./start_tui.sh uv        # 使用 uv run
#
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

export DJANGO_SETTINGS_MODULE=core.settings.dev

if [[ "$1" == "uv" ]]; then
    echo "[*] 启动 TUI Channel (uv)..."
    uv run python manage.py run_tui
else
    echo "[*] 启动 TUI Channel (.venv)..."
    .venv/bin/python manage.py run_tui
fi
