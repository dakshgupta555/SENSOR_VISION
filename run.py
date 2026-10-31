"""Start IC VISION:  python run.py   (then open http://127.0.0.1:5000)"""
import argparse
import threading
import webbrowser

from backend.app import create_app


def main():
    ap = argparse.ArgumentParser(description="IC VISION semiconductor inspection dashboard")
    ap.add_argument("--host", default="127.0.0.1", help="Interface to bind (default 127.0.0.1)")
    ap.add_argument("--port", type=int, default=5000)
    ap.add_argument("--no-browser", action="store_true", help="Do not open a browser window")
    args = ap.parse_args()
    app = create_app()
    url = f"http://{'127.0.0.1' if args.host in ('0.0.0.0', '') else args.host}:{args.port}"
    print(f"\n  IC VISION running at {url}\n  Press Ctrl+C to stop.\n")
    if not args.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    app.run(host=args.host, port=args.port, threaded=True, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
