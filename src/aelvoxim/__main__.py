"""aelvoxim.__main__ — CLI entry point

Usage:
    python -m metacore --lang en
    python -m metacore learn add "Topic name"
    python -m metacore status
"""

import sys
import argparse

from .utils.i18n import set_lang, _


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="MetaCore — Self-evolving AI Agent framework",
    )
    parser.add_argument("--lang", default="en", help="Interface language (en/zh)")
    sub = parser.add_subparsers(dest="command")

    # server — start FastAPI server
    server_p = sub.add_parser("server", help="Start the API server")
    server_p.add_argument("--port", type=int, default=9701, help="Port to listen on")
    server_p.add_argument("--host", type=str, default="127.0.0.1", help="Bind address")
    server_p.add_argument("--reload", action="store_true", help="Enable hot reload")

    # learn
    learn_p = sub.add_parser("learn", help="Manage learning")
    learn_sub = learn_p.add_subparsers(dest="learn_cmd")
    add_p = learn_sub.add_parser("add", help="Add a learning direction")
    add_p.add_argument("topic", help="Topic to learn")
    learn_sub.add_parser("start", help="Start learning loop")
    learn_sub.add_parser("stop", help="Stop learning loop")
    learn_sub.add_parser("status", help="Show learning status")

    # ui
    ui_p = sub.add_parser("ui", help="Start read-only dashboard")
    ui_p.add_argument("--port", type=int, default=9700, help="Dashboard port (default: 9700)")

    # status
    sub.add_parser("status", help="Show system status")

    args = parser.parse_args(argv)
    set_lang(args.lang)

    if args.command == "server":
        _start_server(args.host, args.port, args.reload)
        return

    if args.command == "ui":
        print("Dashboard merged into 9701 at /v1/admin/panel")

    if args.command == "learn":
        cmd = getattr(args, "learn_cmd", None)
        if cmd == "add":
            from .learn.learner import get_learner
            learner = get_learner()
            added = learner.add_direction(args.topic)
            print(f"Direction '{args.topic}' {'added' if added else 'already exists'}")
        elif cmd == "start":
            from .learn.learner import get_learner
            get_learner().start()
            print(_("started"))
        elif cmd == "stop":
            from .learn.learner import get_learner
            get_learner().stop()
            print(_("stopped"))
        elif cmd == "status":
            from .learn.learner import get_learner
            st = get_learner().get_status()
            print(json.dumps(st, indent=2, ensure_ascii=False))
            # Cold-start suggestion
            from .utils import LEARNER_CONFIG
            try:
                cfg = json.loads(LEARNER_CONFIG.read_text())
            except Exception:
                cfg = []
            if not cfg:
                print("---")
                print("No learning directions yet. Try:")
                print('  metacore learn add "FastAPI async optimization patterns"')
                print('  metacore learn add "Python threading safety patterns"')
                print('  metacore learn add "Database index optimization SQLite"')
    elif args.command == "status":
        from .learn.learner import get_learner
        st = get_learner().get_status()
        print(json.dumps(st, indent=2, ensure_ascii=False))
    else:
        parser.print_help()


def _start_server(host: str, port: int, reload: bool) -> None:
    """Start the FastAPI server. Available as 'aelvoxim server --port 9701'."""
    import uvicorn
    import os

    os.environ["AELVOXIM_HOST"] = host

    # Import server module (requires src/ on PYTHONPATH)
    try:
        from aelvoxim.server import create_app
    except ImportError:
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
        from aelvoxim.server import create_app

    app = create_app()
    print(f"Starting Aelvoxim API server on http://{host}:{port}")
    uvicorn.run(app, host=host, port=port, reload=reload)


if __name__ == "__main__":
    main()
