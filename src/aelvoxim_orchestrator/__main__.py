"""
aelvoxim_orchestrator.__main__ — CLI entry point for Orchestrator (9703)

Usage:
    python -m aelvoxim_orchestrator [--port PORT] [--host HOST]
"""
import sys
import os

# Ensure src/ is on path
_THIS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

from aelvoxim_orchestrator.app import start_server

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Aelvoxim Brain Orchestrator (9703)")
    parser.add_argument("--port", type=int, default=9703, help="Listen port")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="Bind address")
    args = parser.parse_args()
    start_server(host=args.host, port=args.port)
