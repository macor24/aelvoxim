#!/usr/bin/env bash
# ── Aelvoxim start script ─────────────────────────────────
# Starts the main API service (9701).
# Dashboard merged into 9701 at /v1/admin/panel.
# SentriKit, ChatAEL, and Gateway are independent projects — start separately.
#
# Usage:
#   ./start.sh              # start API on 9701
#   ./start.sh --daemon     # start in background, no logs to terminal
#   ./start.sh --help       # show usage

set -euo pipefail
cd "$(dirname "$0")"
SRC_DIR="$(cd src && pwd)"

show_help() {
  sed -n '3,10p' "$0"
  exit 0
}

DAEMON=false
for arg in "$@"; do
  case "$arg" in
    --help|-h) show_help ;;
    --daemon|-d) DAEMON=true ;;
  esac
done

# Check dependencies
PYTHON=$(command -v python3 || command -v python)
if [ -z "$PYTHON" ]; then
  echo "❌ Python 3 not found"
  exit 1
fi

if lsof -ti:9701 >/dev/null 2>&1; then
  echo "⚠️  Port 9701 already in use by PID $(lsof -ti:9701)"
fi

# Verify imports
echo "🔍 Verifying imports..."
$PYTHON -c "from aelvoxim import __version__; print(f'  Aelvoxim v{__version__}')" 2>/dev/null || {
  echo "❌ Cannot import aelvoxim — check PYTHONPATH"
  echo "   Try: pip install -e .  or  export PYTHONPATH=src"
  exit 1
}

echo "🚀 Starting Aelvoxim API..."

start_service() {
  local name="$1" cmd="$2" port="$3"
  if [ "$DAEMON" = true ]; then
    nohup $cmd > "$SRC_DIR/../logs/${name}.log" 2>&1 &
    echo "  📡 $name → http://0.0.0.0:$port (PID $!, log: logs/${name}.log)"
  else
    echo "  📡 $name → http://0.0.0.0:$port"
    $cmd &
  fi
}

mkdir -p logs

start_service "API" "$PYTHON -B $SRC_DIR/run_server.py 9701" 9701

echo ""
echo "✅ Aelvoxim v$($PYTHON -c 'from aelvoxim import __version__; print(__version__)') started"
echo "   API:          http://0.0.0.0:9701/ (admin panel at /v1/admin/panel)"
echo "   API docs:     http://0.0.0.0:9701/docs"
echo ""
echo "📌 Independent services (start separately):"
echo "   ChatAEL:      cd frontend/chatael-v2 && python ../../serve_chatael.py"
echo "   SentriKit:    cd /mnt/c/SentriKit/src && python -m sentrikit.api --port 8899"
echo "   Gateway:      cd aelvoxim-gateway && python start_gateway.py"
if [ "$DAEMON" = false ]; then
  echo ""
  echo "Press Ctrl+C to stop all services"
  wait
fi
