#!/usr/bin/env bash
# ── Aelvoxim start script ─────────────────────────────────
# Starts the 2 Aelvoxim services: Dashboard (9700) + API (9701).
# SentriKit and ChatAEL are independent projects — start them separately.
#
# Usage:
#   ./start.sh              # start both services
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

PORTS=(9700 9701)
for port in "${PORTS[@]}"; do
  if lsof -ti:"$port" >/dev/null 2>&1; then
    echo "⚠️  Port $port already in use by PID $(lsof -ti:$port)"
  fi
done

# Verify imports
echo "🔍 Verifying imports..."
$PYTHON -c "from aelvoxim import __version__; print(f'  Aelvoxim v{__version__}')" 2>/dev/null || {
  echo "❌ Cannot import aelvoxim — check PYTHONPATH"
  exit 1
}

echo "🚀 Starting Aelvoxim services..."

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

start_service "Dashboard" "$PYTHON -B -m aelvoxim ui --port 9700" 9700
sleep 2
start_service "API" "$PYTHON -B $SRC_DIR/run_server.py 9701" 9701

echo ""
echo "✅ Aelvoxim v$($PYTHON -c 'from aelvoxim.__version__ import __version__; print(__version__)') started"
echo "   Dashboard:    http://0.0.0.0:9700/"
echo "   API:          http://0.0.0.0:9701/ (cortex merged — formerly 9703)"
echo ""
echo "📌 Independent services (start separately):"
echo "   SentriKit:    cd /mnt/c/SentriKit/src && python -m sentrikit.api --port 8899"
echo "   ChatAEL:      cd /mnt/c/Aelvoxim/frontend/chatael-v2 && python serve.py --port 9702"

if [ "$DAEMON" = false ]; then
  echo ""
  echo "Press Ctrl+C to stop all services"
  wait
fi
