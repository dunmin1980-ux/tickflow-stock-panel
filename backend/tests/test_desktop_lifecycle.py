from __future__ import annotations

import logging
import os
import socket

from app import desktop
from app.config import settings
from app.desktop_runtime import DesktopServer


def test_desktop_server_releases_port_and_thread() -> None:
    server = DesktopServer(port=0)
    server.start()
    assert server.wait_ready(20)
    assert server.bound_port > 0
    assert server._server is not None
    assert server._server.config.host == "127.0.0.1"

    port = server.bound_port
    server.stop(10)

    assert server._thread is not None
    assert not server._thread.is_alive()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", port))


def test_stop_is_idempotent() -> None:
    server = DesktopServer(port=0)
    server.start()
    assert server.wait_ready(20)

    server.stop(10)
    server.stop(10)


def test_stale_instance_lock_is_replaced(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    lock_path = tmp_path / ".desktop.lock"
    lock_path.write_text("999999", encoding="utf-8")

    assert desktop._acquire_single_instance() is True
    assert lock_path.read_text(encoding="utf-8") == str(os.getpid())

    desktop._release_single_instance()
    assert not lock_path.exists()


def test_live_instance_lock_is_rejected(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    lock_path = tmp_path / ".desktop.lock"
    lock_path.write_text(str(os.getpid()), encoding="utf-8")

    assert desktop._acquire_single_instance() is False
    assert lock_path.read_text(encoding="utf-8") == str(os.getpid())


def test_desktop_smoke_mode_stops_server_and_releases_lock(monkeypatch, tmp_path) -> None:
    events: list[str] = []

    class FakeServer:
        bound_port = 43210

        def __init__(self, port: int) -> None:
            assert port == 0

        def start(self) -> None:
            events.append("start")

        def wait_ready(self, timeout: float) -> bool:
            assert timeout == 60
            events.append("ready")
            return True

        def stop(self, timeout: float) -> None:
            assert timeout == 10
            events.append("stop")

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(desktop, "DesktopServer", FakeServer)
    monkeypatch.setenv("TICKFLOW_DESKTOP_SMOKE", "1")

    assert desktop.main() == 0
    assert events == ["start", "ready", "stop"]
    assert not (tmp_path / ".desktop.lock").exists()


def test_top_level_error_is_written_to_desktop_log(monkeypatch, tmp_path) -> None:
    class FailingServer:
        def __init__(self, port: int) -> None:
            assert port == 0

        def start(self) -> None:
            raise RuntimeError("desktop lifecycle sentinel")

        def stop(self, timeout: float) -> None:
            raise AssertionError("server never started")

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(desktop, "DesktopServer", FailingServer)
    monkeypatch.setattr(desktop, "_show_crash", lambda *_args: None)

    try:
        assert desktop.main() == 1
        for handler in logging.getLogger().handlers:
            handler.flush()
        assert "desktop lifecycle sentinel" in (tmp_path / "desktop.log").read_text(
            encoding="utf-8"
        )
    finally:
        for handler in list(logging.getLogger().handlers):
            if isinstance(handler, logging.FileHandler) and handler.baseFilename == str(
                tmp_path / "desktop.log"
            ):
                logging.getLogger().removeHandler(handler)
                handler.close()
