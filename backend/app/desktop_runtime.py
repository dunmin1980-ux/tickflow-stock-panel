"""Controllable loopback-only backend lifecycle for the desktop client."""

from __future__ import annotations

import logging
import threading
import time

import uvicorn

logger = logging.getLogger(__name__)


class DesktopServer:
    """Run the FastAPI application in a non-daemon thread and stop it cleanly."""

    def __init__(self, port: int) -> None:
        if not 0 <= port <= 65535:
            raise ValueError("port must be between 0 and 65535")
        self.requested_port = port
        self.bound_port = port
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None
        self._done = threading.Event()

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("desktop server already started")

        from app.main import app

        config = uvicorn.Config(
            app,
            host="127.0.0.1",
            port=self.requested_port,
            access_log=False,
            log_level="info",
            loop="auto",
        )
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(
            target=self._run,
            name="tickflow-uvicorn",
            daemon=False,
        )
        self._thread.start()

    def _run(self) -> None:
        assert self._server is not None
        try:
            self._server.run()
        except Exception:
            logger.exception("uvicorn desktop backend failed")
        finally:
            self._done.set()

    def _capture_bound_port(self) -> None:
        if self.bound_port != 0 or self._server is None:
            return
        for asyncio_server in getattr(self._server, "servers", ()):
            sockets = asyncio_server.sockets or ()
            if sockets:
                self.bound_port = int(sockets[0].getsockname()[1])
                return

    def wait_ready(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self._capture_bound_port()
            if self.bound_port and self._server is not None and self._server.started:
                return True
            if self._done.is_set():
                return False
            time.sleep(0.1)
        return False

    def stop(self, timeout: float) -> None:
        server = self._server
        thread = self._thread
        if server is None or thread is None:
            return
        server.should_exit = True
        if thread.is_alive():
            thread.join(timeout)
        if thread.is_alive():
            raise RuntimeError("desktop server did not stop")
