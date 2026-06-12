#!/usr/bin/env bash
# 「热读」内容引擎本地联调一键脚本（阶段 4.4：真实 DB 联调）
#
# 职责（依赖 + 迁移，不代起 API/mock 进程）：
#   1. 确保 .env 存在（不存在则从 .env.example 复制）
#   2. docker compose 起 PostgreSQL(pgvector) + Redis 并等待健康
#   3. alembic upgrade head（建全部表，含阶段 4.4 的 9 张运营态表）
#   4. 灌入信源种子（seed_sources）
#   5. 打印 API / mock_server 的手动启动命令（便于各自看日志）
#
# 用法：
#   bash scripts/dev_up.sh
#
# 约定：
#   - PG/Redis 连接串、pgvector 扩展、alembic 配置均已在仓库内就绪，本脚本不重复定义
#   - 运营态种子（会员/报告/推送/RBAC 等）由 mock_server 首次启动经 ensure_seeded 自动写入
#   - 事件正文需另跑采集管线（python -m content_engine.stages.run_all）

set -euo pipefail

# 切到仓库根目录（脚本在 scripts/ 下）
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

log()  { printf '\033[1;36m[dev_up]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[dev_up]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[dev_up]\033[0m %s\n' "$*" >&2; exit 1; }

# ---- 0. 前置检查 ------------------------------------------------------------
command -v docker >/dev/null 2>&1 || die "未找到 docker，请先安装 Docker Desktop"
if docker compose version >/dev/null 2>&1; then
  DC="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
  DC="docker-compose"
else
  die "未找到 docker compose / docker-compose"
fi

# venv 探测：优先用 content_engine/.venv
if [ -x "content_engine/.venv/bin/python" ]; then
  PY="content_engine/.venv/bin/python"
else
  PY="$(command -v python3 || command -v python)"
  warn "未找到 content_engine/.venv，回退使用 $PY（请确认已装依赖）"
fi

# ---- 1. .env ----------------------------------------------------------------
if [ ! -f .env ]; then
  log "未发现 .env，从 .env.example 复制一份"
  cp .env.example .env
fi
# 导出 .env 里的变量，供本脚本与 alembic / seed 子进程使用
set -a
# shellcheck disable=SC1091
. ./.env
set +a
: "${DATABASE_URL:?DATABASE_URL 未在 .env 中设置}"

# ---- 2. 起依赖容器 ----------------------------------------------------------
log "启动 PostgreSQL(pgvector) + Redis 容器"
$DC up -d postgres redis

log "等待 PostgreSQL 健康（最多 60s）"
for i in $(seq 1 60); do
  status="$(docker inspect -f '{{.State.Health.Status}}' rd_postgres 2>/dev/null || echo starting)"
  if [ "$status" = "healthy" ]; then
    log "PostgreSQL 已就绪"
    break
  fi
  [ "$i" -eq 60 ] && die "PostgreSQL 等待超时，请查看：docker logs rd_postgres"
  sleep 1
done

# ---- 3. alembic 迁移 --------------------------------------------------------
log "执行 alembic upgrade head"
"$PY" -m alembic -c content_engine/alembic.ini upgrade head
log "当前迁移版本："
"$PY" -m alembic -c content_engine/alembic.ini current

# ---- 4. 灌入信源种子 --------------------------------------------------------
log "灌入信源种子（seed_sources）"
"$PY" -m content_engine.stages.seed_sources

# ---- 5. 后续手动启动提示 ----------------------------------------------------
cat <<EOF

$(log "依赖 + 迁移 + 信源种子 已就绪 ✅")

下一步（各开一个终端，便于分别看日志；每个终端先 set -a; . ./.env; set +a 以载入连接串）：

  # ① 内容引擎只读 API（FastAPI / uvicorn，默认 :8001）
  set -a; . ./.env; set +a
  $PY -m uvicorn content_engine.api.app:app --reload --port 8001

  # ② Mock 联调服务（读真实 DB，首次启动自动灌运营态种子，默认 :8000）
  set -a; . ./.env; set +a
  $PY mock_server/server.py 8000

  # ③（可选）跑采集管线，生成真实事件内容供前端浏览
  $PY -m content_engine.stages.run_all

提示：
  - 验证 DB 接通：访问 http://localhost:8000/v1/ping，events 数应来自真实库
  - 强制降级 output.json 自测：MOCK_FORCE_JSON=1 $PY mock_server/server.py 8000
  - 停止依赖：$DC down        （销毁数据卷：$DC down -v）
EOF
