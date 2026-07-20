from pathlib import Path

from app import config


def test_frozen_macos_uses_application_support(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "_IS_FROZEN", True)
    monkeypatch.setattr(config.sys, "platform", "darwin")
    monkeypatch.setattr(
        config,
        "user_data_path",
        lambda *a, **k: tmp_path / "TickFlowStockPanel",
    )
    assert config._user_data_root() == tmp_path / "TickFlowStockPanel"


def test_frozen_windows_keeps_executable_data(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "_IS_FROZEN", True)
    monkeypatch.setattr(config.sys, "platform", "win32")
    monkeypatch.setattr(config.sys, "executable", str(tmp_path / "TickFlowStockPanel.exe"))
    assert config._user_data_root() == tmp_path / "data"
