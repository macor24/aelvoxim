"""Start MetaCore FastAPI SaaS server on port 9701."""
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

# Check for uvicorn
try:
    import uvicorn
except ImportError:
    print("This server requires uvicorn. Install with: pip install uvicorn")
    sys.exit(1)

from aelvoxim.server import create_app
from pathlib import Path

# Set up logging with rotation
import logging
from logging.handlers import RotatingFileHandler

_log_dir = Path.home() / ".aelvoxim" / "logs"
_log_dir.mkdir(parents=True, exist_ok=True)
_log_file = _log_dir / "server.log"
_handler = RotatingFileHandler(_log_file, maxBytes=10_485_760, backupCount=5)  # 10MB per file, keep 5
_handler.setFormatter(logging.Formatter("%(asctime)s [%(name)s] %(levelname)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
logging.basicConfig(level=logging.INFO, handlers=[_handler])

# Also log to console
_console = logging.StreamHandler()
_console.setFormatter(logging.Formatter("%(asctime)s [%(name)s] %(levelname)s %(message)s"))
logging.getLogger().addHandler(_console)

app = create_app()

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9701
    reload_enabled = "--reload" in sys.argv or os.environ.get("AELVOXIM_RELOAD", "").lower() == "true"
    print(f"{'🔄 ' if reload_enabled else ''}MetaCore API Server: http://127.0.0.1:{port}")
    print(f"  Register: http://127.0.0.1:{port}/register")
    print(f"  API docs: http://127.0.0.1:{port}/docs")
    print(f"  Press Ctrl+C to stop.")
    host = os.environ.get("AELVOXIM_HOST", "127.0.0.1")
    uvicorn.run(app, host=host, port=port, reload=reload_enabled)
