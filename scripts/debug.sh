#!/bin/sh

set -eu

repository_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$repository_dir"

debug_host=${HIGHLAND_DEBUG_HOST:-127.0.0.1}
debug_port=${HIGHLAND_DEBUG_PORT:-5678}

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is required by the Highland debug environment." >&2
  exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
  echo "Docker Compose v2 is required by the Highland debug environment." >&2
  exit 1
fi

if ! .venv/bin/python -c "import debugpy" >/dev/null 2>&1; then
  echo "Preparing the Python development environment..."
  make setup
fi

if .venv/bin/python -c '
import socket
import sys

with socket.socket() as connection:
    connection.settimeout(0.2)
    raise SystemExit(connection.connect_ex((sys.argv[1], int(sys.argv[2]))) != 0)
' "$debug_host" "$debug_port"; then
  echo "A Highland debug session is already listening at $debug_host:$debug_port." >&2
  echo "Attach VS Code to it, or stop its make debug terminal before starting another." >&2
  exit 2
fi

configured_backend=$(
  .venv/bin/python -c \
    'from highland.settings import HighlandSettings; print(HighlandSettings().model_backend.value)'
)
model_backend=${HIGHLAND_DEBUG_MODEL_BACKEND:-$configured_backend}
case "$model_backend" in
  scripted | cohere) ;;
  *)
    echo "HIGHLAND_DEBUG_MODEL_BACKEND must be 'scripted' or 'cohere'." >&2
    exit 2
    ;;
esac

export PATH="$repository_dir/.venv/bin:$PATH"
export PYTHONPATH="$repository_dir/src${PYTHONPATH:+:$PYTHONPATH}"
export HIGHLAND_MODEL_BACKEND="$model_backend"
export HIGHLAND_HOST=127.0.0.1
export HIGHLAND_PORT=8080

echo "Using Highland debug model backend: $model_backend"
if [ "$model_backend" = "scripted" ]; then
  echo "Scripted chat needs a predefined response queue; arbitrary Discover prompts will not run."
  echo "Set HIGHLAND_MODEL_BACKEND=cohere in .env to debug live model responses."
fi
echo "Starting the web app, mock services, and search index..."
if [ "${HIGHLAND_DEBUG_REBUILD:-0}" = "1" ]; then
  HIGHLAND_MODEL_BACKEND="$model_backend" docker compose up --build --detach --wait
else
  HIGHLAND_MODEL_BACKEND="$model_backend" docker compose up --detach --wait
fi

api_stopped=0
cleanup() {
  debug_status=$?
  trap - EXIT HUP INT TERM

  if [ "$api_stopped" -eq 1 ]; then
    echo "Restoring the containerized Highland API..."
    docker compose start highland-api >/dev/null 2>&1 || :
  fi
  exit "$debug_status"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 0' INT
trap 'exit 143' TERM

echo "Stopping the containerized API so the debugger can use port 8080..."
docker compose stop highland-api
api_stopped=1

echo
echo "Highland is waiting for a debugger at $debug_host:$debug_port."
echo "In VS Code, select 'Highland: Attach to make debug' and press F5."
echo "The workspace will remain available at http://localhost:3000."
echo

.venv/bin/python -Xfrozen_modules=off scripts/debug_app.py
