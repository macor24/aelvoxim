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

# Set up logging for non-learner modules (chat, planner, scheduler)
import logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S")

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
