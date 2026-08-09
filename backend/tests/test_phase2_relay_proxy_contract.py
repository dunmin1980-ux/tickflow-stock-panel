from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
RELAY_PATH = REPO_ROOT / "docker/phase2-canary-relay/relay.py"


@pytest.fixture(scope="module")
def relay_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "phase2_relay_proxy_contract_under_test",
        RELAY_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


class _Connection:
    def __init__(self, error: BaseException) -> None:
        self.error = error
        self.sock = None

    def connect(self) -> None:
        raise self.error

    def close(self) -> None:
        return None


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (ConnectionRefusedError(111, "refused"), "RELAY_PROXY_CONNECT_FAILED"),
        (TimeoutError("deadline"), "RELAY_TIMEOUT"),
    ],
)
def test_relay_proxy_connect_errors_are_not_collapsed_to_timeout(
    relay_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    error: BaseException,
    expected: str,
) -> None:
    monkeypatch.setattr(
        relay_module.http.client,
        "HTTPConnection",
        lambda *_args, **_kwargs: _Connection(error),
    )
    recorder = relay_module.RelayStageRecorder()

    with pytest.raises(relay_module.RelayError) as raised:
        relay_module._default_requester(b"{}", 75, recorder)

    assert raised.value.category == expected
    assert recorder.events()["proxy_connect_started"]["occurred"] is True
    assert recorder.events()["proxy_connect_completed"]["occurred"] is False


def test_relay_receipt_has_one_terminal_reason_source(
    relay_module: ModuleType,
) -> None:
    recorder = relay_module.RelayStageRecorder()
    error = relay_module.RelayError(
        "RELAY_PROXY_CONNECT_FAILED",
        proxy_request_count=1,
    )

    receipt = relay_module._receipt(
        status="REJECTED",
        started_at="2026-08-09T08:00:00Z",
        request_id="a" * 32,
        stage_recorder=recorder,
        error=error,
    )

    assert receipt["terminal_status"] == "RELAY_PROXY_CONNECT_FAILED"
    assert receipt["terminal_reason_source"] == "relay_self"


@pytest.mark.parametrize(
    ("error_category", "terminal_status"),
    [
        ("HTTP_REJECTED", "RELAY_PROTOCOL_REJECTED"),
        ("CANDIDATE_ALREADY_EXISTS", "RELAY_PROCESS_ERROR"),
    ],
)
def test_relay_receipt_maps_detailed_errors_to_closed_terminal_contract(
    relay_module: ModuleType,
    error_category: str,
    terminal_status: str,
) -> None:
    receipt = relay_module._receipt(
        status="REJECTED",
        started_at="2026-08-09T08:00:00Z",
        request_id="a" * 32,
        stage_recorder=relay_module.RelayStageRecorder(),
        error=relay_module.RelayError(error_category),
    )

    assert receipt["terminal_status"] == terminal_status
    assert receipt["error_category"] == error_category
