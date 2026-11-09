#!/usr/bin/env bash
#
# 部署「顶栏行情币种可配置 + 点击弹 K 线」到 txy（腾讯云 43.152.227.67）。
#
# 这次改动包含：
#   - backend/apps/authentication/preferences.py      币种的归一 / 校验 / 默认值
#   - backend/apps/authentication/{models,views,urls}.py  User.header_symbols 与
#       `GET/PUT /api/auth/preferences/`
#   - backend/apps/authentication/migrations/0006_*.py   **有新迁移，必须 migrate**
#   - backend/apps/datasource/ws_runner.py             `ensure_feed_symbols()`：用户改
#       品种时幂等补订阅（ticker 只读 WS 内存存储、不做 REST 回退，漏订阅就是永久「—」）
#   - frontend/.../lib/headerSymbols.ts                常量 / 归一镜像 / 价格口径
#   - frontend/.../hooks/useHeaderSymbols.ts           偏好的读写与同页广播
#   - frontend/.../components/settings/HeaderSymbolsEditor.tsx  配置页编辑器
#   - frontend/.../components/layout/Topbar.tsx        改为消费偏好
#   - frontend/.../components/market/SymbolKlineModal.tsx  点开某档的 K 线弹窗
#
# 与上一版（只加 BNB 那一档）的差别：Topbar 现在 import
# `@/hooks/useHeaderSymbols` 与 `@/lib/headerSymbols`，**只推 Topbar 会推上去一个
# 编译不过的前端**——所以下面第 2 步的清单跟着补齐。
#
# 生效方式不同，这一点是本次的关键：
#   - 后端是 bind mount（./backend:/app），重启即生效 —— 但 WS 行情供给只在进程
#     `ready()` 时订阅一次，**必须 restart 才会把 BNB 加进订阅**（reload 不够）。
#   - 前端构建进镜像（无 mount），必须 `docker compose build frontend`。
#
# 用法：
#   ./scripts/deploy_txy_bnb_kline.sh --dry-run
#   ./scripts/deploy_txy_bnb_kline.sh
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

HOST=txy
REMOTE_DIR="\$HOME/trade_agent_sys"
DRY=""
[[ "${1:-}" == "--dry-run" ]] && DRY="1"

say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
run() {
  if [[ -n "$DRY" ]]; then
    echo "  [dry-run] ssh $HOST: $*"
  else
    ssh "$HOST" "$@"
  fi
}
push() { # push <local-spec> <remote-path> [keep-relative]
  if [[ -n "$DRY" ]]; then
    echo "  [dry-run] rsync $1 -> $HOST:$2"
  else
    rsync -az --delete-delay \
      --exclude='node_modules/' --exclude='.next/' \
      --exclude='.venv/' --exclude='__pycache__/' --exclude='*.pyc' \
      "$1" "$HOST:$2"
  fi
}

# ---------------------------------------------------------------------------
say "0/7 前置检查：主机必须先在用户态活过来"
# ---------------------------------------------------------------------------
# 仓库里有记录的故障形态（.review/halftrend_rsi_修复记录.md，2026-10-19）：
#   ping 通、TCP 22/3000/8000 全部 OPEN，但 SSH banner 与 HTTP 都不出字节。
# 那不是网络问题——TCP 由内核负责（accept 队列照常工作），是目标机用户态进程
# 集体卡死（OOM / 磁盘写满 / load 爆表 / sshd MaxStartups 限流）。
# 三个条件全过才继续；任一不满足就停下，不动服务器上任何文件。
if ! ssh -o ConnectTimeout=20 -o BatchMode=yes "$HOST" 'echo OK' >/dev/null 2>&1; then
  cat >&2 <<'MSG'
❌ SSH 不通（banner 超时）。txy 当前不可部署，**未改动服务器上任何文件**。

   这不是本次改动的问题，是主机本身的状态。诊断结论（与 2026-10-19 那次同一形态）：
     ping 通 + TCP 22/3000/8000 全 OPEN + 但 SSH banner / HTTP 都不出字节
     => 内核在应答 TCP，用户态进程没在调度。

   需要到腾讯云控制台做一次重启（VNC/控制台强制重启），恢复后在本机确认：
     ssh txy 'echo OK && uptime && df -h / && free -g'      # banner 能出 + 资源正常
     curl -s -m 5 http://43.152.227.67:8000/ -o /dev/null -w '%{http_code}\n'   # 非 000
   然后重跑本脚本。
MSG
  exit 1
fi
run 'uptime && echo --- && df -h / | tail -1 && echo --- && free -g | head -2'

say "0b/7 磁盘余量检查（<2G 拒绝部署：写半截文件比不部署更糟）"
avail_kb=$(ssh "$HOST" "df -k / | tail -1 | awk '{print \$4}'")
if [[ "${avail_kb:-0}" -lt 2097152 ]]; then
  echo "❌ / 只剩 $((avail_kb/1024)) MB，先清盘再部署。" >&2
  exit 1
fi
echo "✓ / 可用 $((avail_kb/1024/1024)) GB"

# ---------------------------------------------------------------------------
say "1/7 回滚锚点：记录远端当前 HEAD 与工作区状态"
# ---------------------------------------------------------------------------
run "cd $REMOTE_DIR && git rev-parse --short HEAD && git status --short | head -20"

# ---------------------------------------------------------------------------
say "2/7 上传改动（只同步相关文件，不带 .venv/db/logs/.env）"
# ---------------------------------------------------------------------------
# 后端：偏好层 + 迁移 + 订阅补齐
push "${ROOT}/backend/apps/authentication/preferences.py" \
     "$REMOTE_DIR/backend/apps/authentication/preferences.py"
push "${ROOT}/backend/apps/authentication/models.py" \
     "$REMOTE_DIR/backend/apps/authentication/models.py"
push "${ROOT}/backend/apps/authentication/views.py" \
     "$REMOTE_DIR/backend/apps/authentication/views.py"
push "${ROOT}/backend/apps/authentication/urls.py" \
     "$REMOTE_DIR/backend/apps/authentication/urls.py"
push "${ROOT}/backend/apps/authentication/migrations/0006_user_header_symbols.py" \
     "$REMOTE_DIR/backend/apps/authentication/migrations/0006_user_header_symbols.py"
push "${ROOT}/backend/apps/authentication/tests/test_preferences.py" \
     "$REMOTE_DIR/backend/apps/authentication/tests/test_preferences.py"
push "${ROOT}/backend/apps/datasource/ws_runner.py" \
     "$REMOTE_DIR/backend/apps/datasource/ws_runner.py"
push "${ROOT}/backend/apps/datasource/tests/test_ws_runner.py" \
     "$REMOTE_DIR/backend/apps/datasource/tests/test_ws_runner.py"
# 前端：Topbar 的每一个新依赖都要跟上（漏一个就是构建失败）
push "${ROOT}/frontend/tradeclaw-web/src/lib/headerSymbols.ts" \
     "$REMOTE_DIR/frontend/tradeclaw-web/src/lib/headerSymbols.ts"
push "${ROOT}/frontend/tradeclaw-web/src/lib/api.ts" \
     "$REMOTE_DIR/frontend/tradeclaw-web/src/lib/api.ts"
push "${ROOT}/frontend/tradeclaw-web/src/hooks/useHeaderSymbols.ts" \
     "$REMOTE_DIR/frontend/tradeclaw-web/src/hooks/useHeaderSymbols.ts"
push "${ROOT}/frontend/tradeclaw-web/src/components/layout/Topbar.tsx" \
     "$REMOTE_DIR/frontend/tradeclaw-web/src/components/layout/Topbar.tsx"
push "${ROOT}/frontend/tradeclaw-web/src/components/market/" \
     "$REMOTE_DIR/frontend/tradeclaw-web/src/components/market/"
push "${ROOT}/frontend/tradeclaw-web/src/components/settings/" \
     "$REMOTE_DIR/frontend/tradeclaw-web/src/components/settings/"
push "${ROOT}/frontend/tradeclaw-web/src/app/(dashboard)/settings/page.tsx" \
     "$REMOTE_DIR/frontend/tradeclaw-web/src/app/(dashboard)/settings/page.tsx"

# ---------------------------------------------------------------------------
say "3/7 后端自检：偏好层可 import，且默认值与 WS 订阅清单同源"
# ---------------------------------------------------------------------------
# 在容器内跑（远端宿主机 python 未必装齐依赖）；不上外网，只 import。
# 两条断言各钉一件事：
#   1. `preferences` 能被 import —— 漏推这个文件时，`views.py` 的 import 会炸，
#      整个后端起不来（不是「功能少了」，是 8000 端口不响应）；
#   2. 默认四档 == 订阅清单 —— 两者不同的话，没改过偏好的用户看到四档而订阅只有
#      其中几档，那一档永远是「—」，且不报错。
run "cd $REMOTE_DIR && sudo -n docker compose exec -T backend python -c \"
from apps.authentication.preferences import DEFAULT_HEADER_SYMBOLS, HEADER_SYMBOLS_MIN, HEADER_SYMBOLS_MAX
from apps.datasource.ws_runner import DEFAULT_FEED_SYMBOLS, ensure_feed_symbols
assert DEFAULT_HEADER_SYMBOLS == DEFAULT_FEED_SYMBOLS, (DEFAULT_HEADER_SYMBOLS, DEFAULT_FEED_SYMBOLS)
assert (HEADER_SYMBOLS_MIN, HEADER_SYMBOLS_MAX) == (3, 5), (HEADER_SYMBOLS_MIN, HEADER_SYMBOLS_MAX)
assert callable(ensure_feed_symbols), 'ensure_feed_symbols 缺失'
print('DEFAULT_HEADER_SYMBOLS:', DEFAULT_HEADER_SYMBOLS)
\""

# ---------------------------------------------------------------------------
say "4/7 应用迁移（0006_user_header_symbols）——本次**有**新迁移"
# ---------------------------------------------------------------------------
# 上一版脚本写着「没有新迁移」，那一句对本次是错的：`User.header_symbols` 是新列。
# 不跑这一步，`GET /api/auth/preferences/` 会在 ORM 上 500（列不存在）。
run "cd $REMOTE_DIR && sudo -n docker compose exec -T backend python manage.py migrate authentication"

say "5/7 重启 backend（bind mount 生效 + ready() 重新订阅 WS）"
# ---------------------------------------------------------------------------
# 只重启 backend：celery 的 DATASOURCE_AUTO_WS=false，不订阅行情。
run "cd $REMOTE_DIR && sudo -n docker restart trade_agent_sys-backend-1"

say "等待 backend 起来…"
if [[ -z "$DRY" ]]; then
  ok=""
  for i in $(seq 1 45); do
    code=$(curl -s -m 5 -o /dev/null -w '%{http_code}' http://43.152.227.67:8000/api/ || echo 000)
    if [[ "$code" != "000" ]]; then echo "✓ backend 已应答（HTTP $code，等了 ${i}s）"; ok=1; break; fi
    sleep 2
  done
  if [[ -z "$ok" ]]; then
    echo "❌ backend 90 秒未起来：" >&2
    echo "   sudo docker logs --tail 80 trade_agent_sys-backend-1" >&2
    exit 1
  fi
fi

# ---------------------------------------------------------------------------
say "6/7 重建前端镜像（前端构建进镜像，无 mount，必须 --build）"
# ---------------------------------------------------------------------------
run "cd $REMOTE_DIR && sudo -n docker compose build frontend && sudo -n docker compose up -d frontend"

# ---------------------------------------------------------------------------
say "7/7 验收一：默认四档都订阅上了"
# ---------------------------------------------------------------------------
# 订阅发生在 backend 的 AppConfig.ready()。看日志里那一行 symbols=[...]，
# 这是**唯一**能证明 BNB 进了行情供给的地方——ticker 端点只读 WS 内存存储、
# 不做 REST 回退，没订阅就是永远 503（前端显示成「—」）。
run "sudo -n docker logs trade_agent_sys-backend-1 2>&1 | grep -m2 'feed ready' || true"
run "sudo -n docker logs trade_agent_sys-backend-1 2>&1 | grep -m2 'feed ready' | grep -q 'BNB/USDT' \
     && echo '✓ 日志确认 BNB/USDT 已订阅' \
     || { echo '❌ 日志里没有 BNB/USDT —— BNB 那一档会永远是「—」' >&2; exit 1; }"

# ---------------------------------------------------------------------------
say "验收二：偏好接口可读写，ticker 与 K 线两个端点都给数"
# ---------------------------------------------------------------------------
run "sudo -n docker compose exec -T backend sh -c '
TOKEN=\$(curl -s -X POST http://127.0.0.1:8000/api/auth/login/ \\
  -H \"Content-Type: application/json\" \\
  -d \"{\\\"email\\\":\\\"admin@tradeclaw.local\\\",\\\"password\\\":\\\"admin123\\\"}\" \\
  | python -c \"import sys,json;print(json.load(sys.stdin).get(\\\"access\\\",\\\"\\\"))\")
echo \"--- 偏好 GET（应为默认四档、is_default=true）\"
curl -s -H \"Authorization: Bearer \$TOKEN\" http://127.0.0.1:8000/api/auth/preferences/; echo
echo \"--- 偏好 PUT 两档（应 400，且 error 是人话）\"
curl -s -X PUT -H \"Authorization: Bearer \$TOKEN\" -H \"Content-Type: application/json\" \\
  -d \"{\\\"header_symbols\\\":[\\\"BTC\\\",\\\"ETH\\\"]}\" \\
  http://127.0.0.1:8000/api/auth/preferences/; echo
echo \"--- ticker（顶栏报价）\"
curl -s -o /tmp/t.json -w \"HTTP %{http_code}\\n\" \\
  -H \"Authorization: Bearer \$TOKEN\" \\
  \"http://127.0.0.1:8000/api/datasource/api/market/ticker/?source=binance&symbol=BNB%2FUSDT&market_type=spot\"
head -c 160 /tmp/t.json; echo
echo \"--- klines（弹窗里的图）\"
curl -s -o /tmp/k.json -w \"HTTP %{http_code}\\n\" \\
  -H \"Authorization: Bearer \$TOKEN\" \\
  \"http://127.0.0.1:8000/api/trading/klines/?symbol=BNB%2FUSDT&interval=1h&limit=3\"
head -c 160 /tmp/k.json; echo
'"

say "完成"
cat <<'MSG'

人工确认（浏览器，唯一无法脚本化的部分）：
  1) http://43.152.227.67:3000  → 顶栏应看到四档：BTC / ETH / SOL / BNB，且都有数字与涨跌幅
  2) 点 BNB 那一档 → 弹出 K 线对话框，标题 BNB/USDT，图上有蜡烛 + 成交量
  3) 弹窗里点 4h / 1d 切周期，Esc 或点遮罩关闭

回滚（任一步失败时）：
  ssh txy 'cd ~/trade_agent_sys && git checkout -- backend/apps/datasource/ws_runner.py
            && sudo docker restart trade_agent_sys-backend-1'
  # 前端：git checkout -- frontend/tradeclaw-web/src/components/layout/Topbar.tsx && 重建镜像
MSG
