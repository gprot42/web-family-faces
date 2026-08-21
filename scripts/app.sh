#!/usr/bin/env bash
# =============================================================================
# app.sh — start, stop, restart, status, and debug for Family Faces
#
# Usage:
#   ./scripts/app.sh start
#   ./scripts/app.sh stop
#   ./scripts/app.sh restart
#   ./scripts/app.sh status
#   ./scripts/app.sh debug                 # foreground, API reload, debug logs
#   ./scripts/app.sh logs [--follow] [api|ui|app]
#   ./scripts/app.sh start --port 8750 --ui-port 5180
#   ./scripts/app.sh start --debug --foreground
#   ./scripts/app.sh --help
#
# Commands:
#   start       Run API + UI in the background (default)
#   stop        Stop API + UI (PID files, then our listeners on the ports)
#   restart     Stop, then start
#   status      Show ports, PIDs, and API health
#   debug       Foreground start with uvicorn --reload and debug logs
#   logs        Print recent API, UI, or save/error (app) logs
#
# Options:
#   --port, --api-port N   API port (default 8741, env PHOTOSORT_PORT)
#   --ui-port N            Vite port (default 5174, env PHOTOSORT_UI_PORT)
#   --host HOST            Bind address (default 127.0.0.1, env PHOTOSORT_HOST)
#   --debug                Uvicorn --reload + debug log level
#   --foreground           Keep both processes in this terminal
#   --follow, -f           With logs: tail -F
# =============================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

DEFAULT_HOST="127.0.0.1"
DEFAULT_API_PORT="8741"
DEFAULT_UI_PORT="5174"

HOST="${PHOTOSORT_HOST:-$DEFAULT_HOST}"
API_PORT="${PHOTOSORT_PORT:-$DEFAULT_API_PORT}"
UI_PORT="${PHOTOSORT_UI_PORT:-$DEFAULT_UI_PORT}"
DATA_DIR="${PHOTOSORT_DATA:-$ROOT/data}"
CMD=""
DEBUG=false
FOREGROUND=false
LOG_FOLLOW=false
LOG_WHICH="both"
PORT_EXPLICIT=false
UI_PORT_EXPLICIT=false

RUN_DIR=""
API_PIDFILE=""
UI_PIDFILE=""
STATE_FILE=""
API_LOG=""
UI_LOG=""
APP_LOG=""
API_PID=""
UI_PID=""

die() {
  echo "error: $*" >&2
  exit 1
}

usage() {
  awk '/^# ===/{c++; if(c==2) exit} c==1{sub(/^# ?/,""); print}' "$0"
}

init_paths() {
  RUN_DIR="$DATA_DIR/run"
  API_PIDFILE="$RUN_DIR/api.pid"
  UI_PIDFILE="$RUN_DIR/ui.pid"
  STATE_FILE="$RUN_DIR/ports"
  API_LOG="$DATA_DIR/logs/api.log"
  UI_LOG="$DATA_DIR/logs/ui.log"
  APP_LOG="$DATA_DIR/logs/app.log"
}

listen_pids() {
  lsof -tiTCP:"$1" -sTCP:LISTEN 2>/dev/null || true
}

pid_alive() {
  [[ -n "${1:-}" ]] && kill -0 "$1" 2>/dev/null
}

pid_cmd() {
  ps -p "$1" -o command= 2>/dev/null || true
}

is_our_api_pid() {
  local cmd
  cmd="$(pid_cmd "$1")"
  [[ "$cmd" == *"photosort.main:app"* || "$cmd" == *"uvicorn photosort"* ]]
}

is_our_ui_pid() {
  local cmd
  cmd="$(pid_cmd "$1")"
  [[ "$cmd" == *"vite"* ]] || return 1
  [[ "$cmd" == *"web-family-faces"* || "$cmd" == *"$ROOT/frontend"* || "$cmd" == *"photosort-ui"* ]]
}

ours_on_port() {
  local port="$1" kind="$2" pid
  for pid in $(listen_pids "$port"); do
    case "$kind" in
      api) is_our_api_pid "$pid" && echo "$pid" ;;
      ui)  is_our_ui_pid "$pid" && echo "$pid" ;;
    esac
  done
}

first_line() {
  local line=""
  IFS= read -r line || true
  printf '%s' "$line"
}

saved_host=""
saved_api_port=""
saved_ui_port=""

load_saved_ports() {
  saved_host=""
  saved_api_port=""
  saved_ui_port=""
  [[ -f "$STATE_FILE" ]] || return 0
  saved_host="$(awk -F= '/^HOST=/{gsub(/'\''/, "", $2); print $2; exit}' "$STATE_FILE")"
  saved_api_port="$(awk -F= '/^API_PORT=/{gsub(/'\''/, "", $2); print $2; exit}' "$STATE_FILE")"
  saved_ui_port="$(awk -F= '/^UI_PORT=/{gsub(/'\''/, "", $2); print $2; exit}' "$STATE_FILE")"
}

save_state() {
  mkdir -p "$RUN_DIR"
  cat >"$STATE_FILE" <<EOF
HOST='$HOST'
API_PORT='$API_PORT'
UI_PORT='$UI_PORT'
EOF
}

ensure_venv() {
  [[ -x "$ROOT/.venv/bin/python" ]] || die "missing .venv — create it with python3.12 -m venv .venv and pip install -r backend/requirements.txt"
}

ensure_frontend() {
  if [[ ! -d "$ROOT/frontend/node_modules" ]]; then
    echo "→ installing frontend dependencies"
    (cd "$ROOT/frontend" && npm install)
  fi
}

write_pid() {
  mkdir -p "$RUN_DIR"
  echo "$2" >"$1"
}

read_pid() {
  [[ -f "$1" ]] || return 0
  tr -d ' \n' <"$1"
}

kill_pid_tree() {
  local pid="$1"
  pid_alive "$pid" || return 0
  kill -TERM "$pid" 2>/dev/null || true
  local i
  for i in 1 2 3 4 5 6 7 8 9 10; do
    pid_alive "$pid" || return 0
    sleep 0.2
  done
  kill -KILL "$pid" 2>/dev/null || true
}

wait_port_free() {
  local port="$1" i
  for i in $(seq 1 25); do
    [[ -z "$(listen_pids "$port")" ]] && return 0
    sleep 0.2
  done
  return 1
}

stop_kind() {
  local kind="$1" port="$2" pidfile="$3"
  local pid recorded leftover

  recorded="$(read_pid "$pidfile" || true)"
  if pid_alive "$recorded"; then
    echo "→ stopping $kind pid $recorded"
    kill_pid_tree "$recorded"
  fi
  rm -f "$pidfile"

  leftover="$(ours_on_port "$port" "$kind")"
  if [[ -n "$leftover" ]]; then
    echo "→ stopping $kind on :$port ($(echo "$leftover" | tr '\n' ' '))"
    for pid in $leftover; do
      kill_pid_tree "$pid"
    done
  fi

  if ! wait_port_free "$port"; then
    leftover="$(listen_pids "$port")"
    if [[ -n "$leftover" ]]; then
      die "port $port still in use: $(echo "$leftover" | tr '\n' ' ')"
    fi
  fi
}

unique_ports() {
  local seen=" " p
  for p in "$@"; do
    [[ -n "$p" ]] || continue
    [[ "$seen" == *" $p "* ]] && continue
    seen="$seen$p "
    echo "$p"
  done
}

cmd_stop() {
  init_paths
  load_saved_ports

  local api_ports ui_ports
  api_ports="$(unique_ports "$API_PORT" "$saved_api_port" "$DEFAULT_API_PORT")"
  ui_ports="$(unique_ports "$UI_PORT" "$saved_ui_port" "$DEFAULT_UI_PORT")"

  local p
  for p in $api_ports; do
    stop_kind api "$p" "$API_PIDFILE"
  done
  for p in $ui_ports; do
    stop_kind ui "$p" "$UI_PIDFILE"
  done
  echo "→ stopped"
}

describe_foreign() {
  local pids="$1"
  [[ -n "$pids" ]] || return 0
  # shellcheck disable=SC2086
  ps -p $pids -o pid=,command= 2>/dev/null | sed 's/^/  /' || true
}

port_conflict() {
  local port="$1" kind="$2" pids holder
  pids="$(listen_pids "$port")"
  [[ -z "$pids" ]] && return 0
  holder="$(ours_on_port "$port" "$kind" | first_line)"
  if [[ -n "$holder" ]]; then
    return 1
  fi
  echo "error: port $port is in use by another process:" >&2
  describe_foreign "$pids" >&2
  exit 1
}

our_api_pid() {
  ours_on_port "$API_PORT" api | first_line
}

our_ui_pid() {
  ours_on_port "$UI_PORT" ui | first_line
}

launch_api() {
  mkdir -p "$(dirname "$API_LOG")" "$RUN_DIR"
  cd "$ROOT"
  export PYTHONPATH="$ROOT/backend"
  export PHOTOSORT_DATA="$DATA_DIR"
  export PHOTOSORT_HOST="$HOST"
  export PHOTOSORT_PORT="$API_PORT"
  export PHOTOSORT_UI_PORT="$UI_PORT"
  export OMP_NUM_THREADS="${OMP_NUM_THREADS:-2}"
  export OPENBLAS_NUM_THREADS="${OPENBLAS_NUM_THREADS:-1}"
  export MKL_NUM_THREADS="${MKL_NUM_THREADS:-1}"
  export VECLIB_MAXIMUM_THREADS="${VECLIB_MAXIMUM_THREADS:-1}"
  export NUMEXPR_NUM_THREADS="${NUMEXPR_NUM_THREADS:-1}"
  export ORT_INTRA_OP_NUM_THREADS="${ORT_INTRA_OP_NUM_THREADS:-2}"
  export ORT_INTER_OP_NUM_THREADS="${ORT_INTER_OP_NUM_THREADS:-1}"
  if [[ "$DEBUG" == true ]]; then
    exec "$ROOT/.venv/bin/python" -m uvicorn photosort.main:app \
      --host "$HOST" --port "$API_PORT" \
      --reload --reload-dir "$ROOT/backend/photosort" \
      --log-level debug
  fi
  exec "$ROOT/.venv/bin/python" -m uvicorn photosort.main:app \
    --host "$HOST" --port "$API_PORT"
}

launch_ui() {
  mkdir -p "$(dirname "$UI_LOG")" "$RUN_DIR"
  cd "$ROOT/frontend"
  export PHOTOSORT_HOST="$HOST"
  export PHOTOSORT_PORT="$API_PORT"
  export PHOTOSORT_UI_PORT="$UI_PORT"
  exec "$ROOT/frontend/node_modules/.bin/vite" \
    --host "$HOST" --port "$UI_PORT" --strictPort
}

wait_api() {
  local i
  for i in $(seq 1 60); do
    if curl -sf --max-time 1 "http://${HOST}:${API_PORT}/api/health" >/dev/null 2>&1; then
      return 0
    fi
    if [[ -n "$API_PID" ]] && ! pid_alive "$API_PID"; then
      return 1
    fi
    sleep 0.25
  done
  return 1
}

wait_ui() {
  local i
  for i in $(seq 1 40); do
    if [[ -n "$(listen_pids "$UI_PORT")" ]]; then
      return 0
    fi
    if [[ -n "$UI_PID" ]] && ! pid_alive "$UI_PID"; then
      return 1
    fi
    sleep 0.15
  done
  return 1
}

fail_start() {
  local which="$1" log="$2"
  echo "$which failed to start — last log lines:" >&2
  tail -n 40 "$log" >&2 || true
  stop_kind api "$API_PORT" "$API_PIDFILE"
  stop_kind ui "$UI_PORT" "$UI_PIDFILE"
  exit 1
}

cmd_start() {
  ensure_venv
  ensure_frontend
  init_paths
  mkdir -p "$DATA_DIR/logs" "$RUN_DIR"

  local api_now ui_now
  api_now="$(our_api_pid)"
  ui_now="$(our_ui_pid)"

  if [[ -n "$api_now" && -n "$ui_now" && "$FOREGROUND" == false ]]; then
    echo "Family Faces is already running"
    cmd_status || true
    exit 0
  fi

  if [[ -n "$api_now" || -n "$ui_now" ]]; then
    echo "→ partial instance found, restarting"
    cmd_stop
  fi

  port_conflict "$API_PORT" api
  port_conflict "$UI_PORT" ui
  save_state

  echo "→ starting API on ${HOST}:${API_PORT} (debug=$DEBUG)"
  launch_api >>"$API_LOG" 2>&1 &
  API_PID=$!
  write_pid "$API_PIDFILE" "$API_PID"

  echo "→ starting UI  on ${HOST}:${UI_PORT}"
  launch_ui >>"$UI_LOG" 2>&1 &
  UI_PID=$!
  write_pid "$UI_PIDFILE" "$UI_PID"

  if [[ "$FOREGROUND" == true ]]; then
    cleanup_fg() {
      trap - EXIT INT TERM
      stop_kind api "$API_PORT" "$API_PIDFILE"
      stop_kind ui "$UI_PORT" "$UI_PIDFILE"
    }
    trap cleanup_fg EXIT INT TERM
  fi

  if ! wait_api; then
    fail_start "API" "$API_LOG"
  fi
  if ! wait_ui; then
    fail_start "UI" "$UI_LOG"
  fi

  echo "Family Faces is running"
  echo "  UI   http://${HOST}:${UI_PORT}"
  echo "  API  http://${HOST}:${API_PORT}"
  echo "  logs $API_LOG"
  echo "       $UI_LOG"
  echo "  stop ./scripts/app.sh stop"

  if [[ "$FOREGROUND" == true ]]; then
    echo "→ foreground (Ctrl+C to stop)"
    tail -n +1 -F "$API_LOG" "$UI_LOG"
  fi
}

cmd_status() {
  init_paths
  load_saved_ports
  if [[ "$PORT_EXPLICIT" == false && -n "$saved_api_port" ]]; then
    API_PORT="$saved_api_port"
  fi
  if [[ "$UI_PORT_EXPLICIT" == false && -n "$saved_ui_port" ]]; then
    UI_PORT="$saved_ui_port"
  fi
  if [[ -n "$saved_host" && "$HOST" == "$DEFAULT_HOST" ]]; then
    HOST="$saved_host"
  fi

  local api_pid ui_pid health="down" ok=1
  api_pid="$(our_api_pid)"
  ui_pid="$(our_ui_pid)"
  if [[ -z "$api_pid" ]]; then
    api_pid="$(read_pid "$API_PIDFILE" || true)"
    pid_alive "$api_pid" || api_pid=""
  fi
  if [[ -z "$ui_pid" ]]; then
    ui_pid="$(read_pid "$UI_PIDFILE" || true)"
    pid_alive "$ui_pid" || ui_pid=""
  fi

  if curl -sf --max-time 2 "http://${HOST}:${API_PORT}/api/health" >/dev/null 2>&1; then
    health="ok"
  fi

  echo "Family Faces"
  echo "  host     $HOST"
  echo "  API      :$API_PORT  pid ${api_pid:-—}  health $health"
  if pid_alive "${api_pid:-}"; then
    echo "           $(pid_cmd "$api_pid")"
  elif [[ -z "$(listen_pids "$API_PORT")" ]]; then
    echo "           (not running)"
  else
    echo "           (something else is listening)"
    describe_foreign "$(listen_pids "$API_PORT")" | sed 's/^/         /'
  fi
  echo "  UI       :$UI_PORT  pid ${ui_pid:-—}"
  if pid_alive "${ui_pid:-}"; then
    echo "           $(pid_cmd "$ui_pid")"
  elif [[ -z "$(listen_pids "$UI_PORT")" ]]; then
    echo "           (not running)"
  else
    echo "           (something else is listening)"
    describe_foreign "$(listen_pids "$UI_PORT")" | sed 's/^/         /'
  fi
  echo "  UI URL   http://${HOST}:${UI_PORT}"
  echo "  API URL  http://${HOST}:${API_PORT}"

  if [[ "$health" == "ok" ]] && pid_alive "${ui_pid:-}"; then
    ok=0
  fi
  return "$ok"
}

cmd_logs() {
  init_paths
  local files=""
  case "$LOG_WHICH" in
    api) files="$API_LOG" ;;
    ui)  files="$UI_LOG" ;;
    app) files="$APP_LOG" ;;
    *)   files="$API_LOG $UI_LOG $APP_LOG" ;;
  esac
  local f existing=""
  for f in $files; do
    if [[ -f "$f" ]]; then
      existing="$existing $f"
    else
      echo "no log yet: $f" >&2
    fi
  done
  [[ -n "$existing" ]] || exit 1
  if [[ "$LOG_FOLLOW" == true ]]; then
    # shellcheck disable=SC2086
    tail -n 50 -F $existing
  else
    for f in $existing; do
      echo "===== $f ====="
      tail -n 50 "$f"
    done
  fi
}

cmd_restart() {
  cmd_stop
  cmd_start
}

next_arg() {
  local idx="$1"
  if [[ "$idx" -ge ${#args[@]} ]]; then
    die "${args[$((idx - 1))]} needs a value"
  fi
  printf '%s' "${args[$idx]}"
}

args=("$@")
i=0
while [[ $i -lt ${#args[@]} ]]; do
  arg="${args[$i]}"
  case "$arg" in
    -h|--help)
      usage
      exit 0
      ;;
    start|stop|restart|status|debug|logs)
      [[ -z "$CMD" ]] || die "multiple commands: $CMD and $arg"
      CMD="$arg"
      i=$((i + 1))
      ;;
    --port|--api-port)
      API_PORT="$(next_arg $((i + 1)))"
      PORT_EXPLICIT=true
      i=$((i + 2))
      ;;
    --ui-port)
      UI_PORT="$(next_arg $((i + 1)))"
      UI_PORT_EXPLICIT=true
      i=$((i + 2))
      ;;
    --host)
      HOST="$(next_arg $((i + 1)))"
      i=$((i + 2))
      ;;
    --debug)
      DEBUG=true
      i=$((i + 1))
      ;;
    --foreground)
      FOREGROUND=true
      i=$((i + 1))
      ;;
    --follow|-f)
      LOG_FOLLOW=true
      i=$((i + 1))
      ;;
    api|ui|app|both)
      if [[ "$CMD" == "logs" || -z "$CMD" ]]; then
        LOG_WHICH="$arg"
        i=$((i + 1))
      else
        die "unknown argument: $arg (try --help)"
      fi
      ;;
    *)
      die "unknown argument: $arg (try --help)"
      ;;
  esac
done

case "${CMD:-}" in
  "") usage; exit 1 ;;
  start) cmd_start ;;
  stop) cmd_stop ;;
  restart) cmd_restart ;;
  status) cmd_status ;;
  debug)
    DEBUG=true
    FOREGROUND=true
    cmd_start
    ;;
  logs) cmd_logs ;;
esac
