"""
Desktop entry point for PyInstaller bundle.

Launches a local Streamlit server on a random free port and opens the user's
default browser. The Streamlit process runs in-process (via streamlit.web.cli),
so closing the OS app window stops the server.
"""

from __future__ import annotations

import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path


def _find_free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _resource_path(rel: str) -> str:
    """Resolve a path inside the frozen bundle or the source checkout."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return str(base / rel)


def _open_browser_later(url: str, delay: float = 2.0) -> None:
    def _run() -> None:
        time.sleep(delay)
        webbrowser.open(url)

    threading.Thread(target=_run, daemon=True).start()


def main() -> None:
    port = _find_free_port()
    app_path = _resource_path("app.py")
    url = f"http://127.0.0.1:{port}"

    sys.argv = [
        "streamlit",
        "run",
        app_path,
        "--server.address",
        "127.0.0.1",
        "--server.port",
        str(port),
        "--server.headless",
        "true",
        "--browser.gatherUsageStats",
        "false",
        "--global.developmentMode",
        "false",
    ]

    _open_browser_later(url)

    from streamlit.web import cli as stcli

    sys.exit(stcli.main())


if __name__ == "__main__":
    main()
