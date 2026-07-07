#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
Aelvoxim Desktop Gateway — Universal desktop software adapter.

Standalone process, communicates with Aelvoxim brain via HTTP/WebSocket.
Can be started/stopped independently, no dependency on Aelvoxim brain.

Usage:
    python main.py [--port PORT] [--host HOST] [--config PATH]

Examples:
    python main.py                        # Default port 9705
    python main.py --port 9705 --host 0.0.0.0
"""
from __future__ import annotations

import argparse
import sys
import os

# Ensure package is importable
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)


def main():
    parser = argparse.ArgumentParser(
        description="Aelvoxim Desktop Gateway — universal desktop software adapter"
    )
    parser.add_argument("--port", type=int, default=0,
                        help="HTTP server port (default: from config, 9705)")
    parser.add_argument("--host", type=str, default="",
                        help="Bind address (default: from config, 127.0.0.1)")
    parser.add_argument("--config", type=str, default="",
                        help="Path to config.yaml (default: ./config.yaml)")
    args = parser.parse_args()

    # Load config
    import gateway.config as cfg
    if args.config:
        cfg.load(args.config)
    else:
        cfg.load()

    port = args.port or cfg.gateway_port()
    host = args.host or cfg.gateway_host()

    # Ensure temp dir exists
    temp_dir = cfg.temp_dir()
    temp_dir.mkdir(parents=True, exist_ok=True)

    print("╔══════════════════════════════════════════╗")
    print("║     Aelvoxim Desktop Gateway (FastAPI)   ║")
    print("╚══════════════════════════════════════════╝")
    print()

    from gateway.server import start_server
    start_server(host=host, port=port)


if __name__ == "__main__":
    main()
