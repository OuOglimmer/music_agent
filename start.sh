#!/usr/bin/env bash
#
# 音乐 Agent 一键启动脚本
#
# 用法:
#   ./start.sh              # 全部启动: docker db -> 后端(8000) -> 前端(3000)
#   ./start.sh backend      # 只启动后端
#   ./start.sh frontend     # 只启动前端
#   ./start.sh stop         # 停止由本脚本启动的前后端进程
#   ./start.sh status       # 查看各服务状态
#   ./start.sh logs [backend|frontend]   # 跟踪日志
#
# 选项:
#   --no-db       不启动/不等待 Docker 里的 PostgreSQL(例如用 Windows 宿主机数据库)
#   --reinstall   前端强制重新 pnpm install
#
# 日志: logs/backend.log  logs/frontend.log
# PID : .run/backend.pid  .run/frontend.pid

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_DIR="$ROOT/.run"
LOG_DIR="$ROOT/logs"
BACKEND_PID_FILE="$RUN_DIR/backend.pid"
FRONTEND_PID_FILE="$RUN_DIR/frontend.pid"
BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-3000}"
BACKEND_URL="${BACKEND_URL:-http://localhost:${BACKEND_PORT}}"
FRONTEND_URL="${FRONTEND_URL:-http://localhost:${FRONTEND_PORT}}"

mkdir -p "$RUN_DIR" "$LOG_DIR"

# ---------------------------------------------------------------- 颜色与输出

if [ -t 1 ]; then
  C_GREEN=$'\033[32m'; C_YELLOW=$'\033[33m'; C_RED=$'\033[31m'; C_DIM=$'\033[2m'; C_RESET=$'\033[0m'
else
  C_GREEN=""; C_YELLOW=""; C_RED=""; C_DIM=""; C_RESET=""
fi
info()  { printf '%s[INFO]%s %s\n'  "$C_GREEN"  "$C_RESET" "$*"; }
warn()  { printf '%s[WARN]%s %s\n'  "$C_YELLOW" "$C_RESET" "$*"; }
error() { printf '%s[FAIL]%s %s\n'  "$C_RED"    "$C_RESET" "$*" >&2; }

# ---------------------------------------------------------------- 工具函数

pid_of() {  # 读取 pidfile，进程仍存活则输出 pid，否则清掉失效文件
  local file="$1" pid
  [ -f "$file" ] || return 1
  pid="$(cat "$file" 2>/dev/null || true)"
  if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
    echo "$pid"
  else
    rm -f "$file"
    return 1
  fi
}

port_in_use() { ss -ltn "( sport = :$1 )" 2>/dev/null | grep -q LISTEN; }

wait_http() {  # wait_http <url> <timeout秒> <服务名>
  local url="$1" timeout="$2" name="$3" i
  info "等待 $name 就绪 ($url) ..."
  for ((i = 0; i < timeout; i += 2)); do
    if curl -sf -o /dev/null "$url"; then
      info "$name 已就绪"
      return 0
    fi
    sleep 2
  done
  error "$name 在 ${timeout}s 内未就绪，请查看日志: tail -f $LOG_DIR"
  return 1
}

start_service() {  # start_service <名称> <pidfile> <工作目录> <日志文件> <命令...>
  local name="$1" pidfile="$2" workdir="$3" logfile="$4"
  shift 4
  (
    cd "$workdir"
    nohup setsid "$@" >"$logfile" 2>&1 &
    echo $! >"$pidfile"
  )
  info "$name 已启动 (PID $(cat "$pidfile"))，日志: $logfile"
}

stop_service() {  # stop_service <名称> <pidfile>
  local name="$1" pidfile="$2" pid pgid
  if pid="$(pid_of "$pidfile")"; then
    pgid="$(ps -o pgid= -p "$pid" 2>/dev/null | tr -d ' ' || true)"
    if [ -n "$pgid" ]; then
      kill -- -"$pgid" 2>/dev/null || true
    else
      kill "$pid" 2>/dev/null || true
    fi
    for _ in 1 2 3 4 5 6 7 8 9 10; do
      kill -0 "$pid" 2>/dev/null || break
      sleep 1
    done
    if kill -0 "$pid" 2>/dev/null; then
      warn "$name 未能正常退出，强制结束"
      [ -n "${pgid:-}" ] && kill -9 -- -"$pgid" 2>/dev/null || kill -9 "$pid" 2>/dev/null || true
    fi
    rm -f "$pidfile"
    info "$name 已停止"
  else
    info "$name 未在运行 (无本脚本记录的进程)"
  fi
}

find_pnpm() {  # 依次尝试 pnpm / corepack pnpm
  if command -v pnpm >/dev/null 2>&1; then
    PNPM=(pnpm)
  elif command -v corepack >/dev/null 2>&1; then
    PNPM=(corepack pnpm)
  else
    error "未找到 pnpm，请先安装: npm i -g pnpm (或 corepack enable)"
    exit 1
  fi
}

# ---------------------------------------------------------------- 基础设施

ensure_db() {
  if ! command -v docker >/dev/null 2>&1; then
    warn "未安装 docker，跳过数据库启动；请确认 .env 里 DATABASE_URL 指向可用的 PostgreSQL"
    return 0
  fi
  if ! docker compose ps --status running db 2>/dev/null | grep -q db; then
    info "启动 PostgreSQL (docker compose)..."
    docker compose -f "$ROOT/compose.yml" -f "$ROOT/compose.override.yml" up -d db
  else
    info "PostgreSQL 容器已在运行"
  fi
  info "等待 PostgreSQL 健康检查通过..."
  for _ in $(seq 1 30); do
    local db_cid
    db_cid="$(docker compose -f "$ROOT/compose.yml" -f "$ROOT/compose.override.yml" ps -q db 2>/dev/null || true)"
    if [ -n "$db_cid" ] && [ "$(docker inspect -f '{{.State.Health.Status}}' "$db_cid" 2>/dev/null)" = "healthy" ]; then
      info "PostgreSQL 已就绪"
      return 0
    fi
    sleep 2
  done
  error "PostgreSQL 未通过健康检查，请执行: docker compose logs db"
  return 1
}

ensure_mailpit() {  # 本地邮件测试，可选项，失败不影响主流程
  command -v docker >/dev/null 2>&1 || return 0
  if ! port_in_use 8025 && ! docker compose ps --status running mailpit 2>/dev/null | grep -q mailpit; then
    if timeout 120 docker compose -f "$ROOT/compose.yml" -f "$ROOT/compose.override.yml" up -d mailpit >/dev/null 2>&1; then
      info "Mailpit 已启动 (邮件预览: http://localhost:8025)"
    else
      warn "Mailpit 启动失败(首次需拉取镜像)，不影响主流程；忘记密码等邮件将无法查看"
    fi
  fi
}

# ---------------------------------------------------------------- 各服务

start_backend() {
  if pid="$(pid_of "$BACKEND_PID_FILE")"; then
    info "后端已在本脚本管理下运行 (PID $pid)"
    return 0
  fi
  if port_in_use "$BACKEND_PORT"; then
    warn "端口 $BACKEND_PORT 已被占用(可能已有后端在运行，或被其他程序占用)，跳过后端启动"
    return 0
  fi
  command -v uv >/dev/null 2>&1 || { error "未找到 uv，请先安装: curl -LsSf https://astral.sh/uv/install.sh | sh"; return 1; }

  info "同步后端依赖 (uv sync)..."
  (cd "$ROOT/backend" && uv sync)
  info "准备数据库 (迁移 + 初始超级用户)..."
  (cd "$ROOT/backend" && uv run bash scripts/prestart.sh) >"$LOG_DIR/backend-setup.log" 2>&1 \
    || { error "prestart 失败，详见 $LOG_DIR/backend-setup.log"; return 1; }

  start_service "后端" "$BACKEND_PID_FILE" "$ROOT/backend" "$LOG_DIR/backend.log" \
    uv run fastapi dev --port "$BACKEND_PORT"
  wait_http "$BACKEND_URL/api/v1/utils/health-check/" 60 "后端 API"
}

start_frontend() {
  if pid="$(pid_of "$FRONTEND_PID_FILE")"; then
    info "前端已在本脚本管理下运行 (PID $pid)"
    return 0
  fi
  if port_in_use "$FRONTEND_PORT"; then
    warn "端口 $FRONTEND_PORT 已被占用(可能已有前端在运行，或被其他程序占用)，跳过前端启动"
    return 0
  fi
  command -v node >/dev/null 2>&1 || { error "未找到 Node.js (需要 >= 20)"; return 1; }
  find_pnpm

  if [ ! -d "$ROOT/frontend/node_modules" ] || [ "${REINSTALL:-0}" = "1" ]; then
    info "安装前端依赖 (pnpm install)..."
    (cd "$ROOT/frontend" && "${PNPM[@]}" install)
  else
    info "前端依赖已安装，跳过 pnpm install (--reinstall 可强制重装)"
  fi

  start_service "前端" "$FRONTEND_PID_FILE" "$ROOT/frontend" "$LOG_DIR/frontend.log" \
    "${PNPM[@]}" dev --port "$FRONTEND_PORT"
  wait_http "$FRONTEND_URL" 90 "前端"
}

show_summary() {
  echo
  echo -e "${C_GREEN}========================================${C_RESET}"
  echo "  音乐 Agent 启动完成"
  echo -e "${C_GREEN}========================================${C_RESET}"
  echo "  前端工作台   : $FRONTEND_URL        (入口 /dashboard, /chat)"
  echo "  后端 API     : $BACKEND_URL"
  echo "  接口文档     : $BACKEND_URL/docs"
  echo "  Mailpit 邮件 : http://localhost:8025"
  echo "  超级管理员   : $(grep -oP '^FIRST_SUPERUSER=\K.*' "$ROOT/.env" 2>/dev/null || echo '见 .env')"
  echo -e "${C_DIM}  日志: tail -f logs/backend.log logs/frontend.log${C_RESET}"
  echo -e "${C_DIM}  停止: ./start.sh stop${C_RESET}"
  echo
}

show_status() {
  echo "== 数据库 =="
  if command -v docker >/dev/null 2>&1; then
    docker compose -f "$ROOT/compose.yml" -f "$ROOT/compose.override.yml" ps db mailpit 2>/dev/null || true
  else
    echo "docker 不可用"
  fi
  echo "== 后端 ($BACKEND_PORT) =="
  if pid="$(pid_of "$BACKEND_PID_FILE")"; then
    echo "运行中 (PID $pid, 本脚本启动)"
  elif port_in_use 8000; then
    echo "端口 8000 有服务在监听 (非本脚本启动)"
  else
    echo "未运行"
  fi
  echo "== 前端 ($FRONTEND_PORT) =="
  if pid="$(pid_of "$FRONTEND_PID_FILE")"; then
    echo "运行中 (PID $pid, 本脚本启动)"
  elif port_in_use 3000; then
    echo "端口 3000 有服务在监听 (非本脚本启动)"
  else
    echo "未运行"
  fi
}

show_logs() {
  local target="${1:-all}"
  case "$target" in
    backend)  tail -n 50 -f "$LOG_DIR/backend.log" ;;
    frontend) tail -n 50 -f "$LOG_DIR/frontend.log" ;;
    all)      tail -n 50 -f "$LOG_DIR/backend.log" "$LOG_DIR/frontend.log" ;;
    *)        error "用法: $0 logs [backend|frontend]"; exit 1 ;;
  esac
}

# ---------------------------------------------------------------- 参数解析

TARGET="all"
NO_DB=0
REINSTALL=0
for arg in "$@"; do
  case "$arg" in
    all|backend|frontend|stop|status|logs) TARGET="$arg" ;;
    --no-db)     NO_DB=1 ;;
    --reinstall) REINSTALL=1; export REINSTALL ;;
    -h|--help)   sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) error "未知参数: $arg (用 --help 查看用法)"; exit 1 ;;
  esac
done

case "$TARGET" in
  all)
    [ "$NO_DB" = "1" ] || ensure_db
    ensure_mailpit
    start_backend
    start_frontend
    show_summary
    ;;
  backend)
    [ "$NO_DB" = "1" ] || ensure_db
    ensure_mailpit
    start_backend
    ;;
  frontend)
    start_frontend
    ;;
  stop)
    stop_service "前端" "$FRONTEND_PID_FILE"
    stop_service "后端" "$BACKEND_PID_FILE"
    ;;
  status)  show_status ;;
  logs)    show_logs "${2:-all}" ;;
esac
