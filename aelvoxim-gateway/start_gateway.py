# AEL Gateway launcher
import uvicorn
from gateway.server import app
uvicorn.run(app, host="0.0.0.0", port=9705, log_level="info")
