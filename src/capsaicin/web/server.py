"""Server launcher for the local operator UI.

Handles port selection, browser auto-open, and uvicorn startup.
"""

from __future__ import annotations

import socket
import threading
import webbrowser
from pathlib import Path


def find_open_port(host: str = "127.0.0.1") -> int:
    """Ask the OS for an available ephemeral port on *host*.

    Binds to port 0, reads the assigned port, then closes the socket.
    This is reliable regardless of how many ports are already in use.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return s.getsockname()[1]


def _open_browser(url: str, delay: float = 1.0) -> None:
    """Open *url* in the default browser after a short delay."""
    import time

    def _open():
        time.sleep(delay)
        webbrowser.open(url)

    t = threading.Thread(target=_open, daemon=True)
    t.start()


def run_server(
    db_path: str | Path,
    project_id: str,
    config_path: str | Path,
    log_path: str | Path,
    host: str = "127.0.0.1",
    port: int | None = None,
    open_browser: bool = True,
) -> None:
    """Start the uvicorn server with the capsaicin ASGI app."""
    import uvicorn

    from capsaicin.web.app import create_app

    if port is None:
        port = find_open_port(host)

    app = create_app(
        db_path=db_path,
        project_id=project_id,
        config_path=config_path,
        log_path=log_path,
    )

    url = f"http://{host}:{port}"

    if open_browser:
        _open_browser(url)

    print(f"Capsaicin UI running at {url}")
    print("Press Ctrl+C to stop.")

    uvicorn.run(app, host=host, port=port, log_level="warning")
