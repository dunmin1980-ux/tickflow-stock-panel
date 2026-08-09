from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

from app.services.phase2_claims_service import canonical_json_bytes

REPO_ROOT = Path(__file__).resolve().parents[2]
PROXY_PATH = REPO_ROOT / "docker/phase2-openai-egress-proxy/proxy.py"
REQUEST_ID = "a" * 32


@pytest.fixture(scope="module")
def proxy_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "phase2_proxy_readiness_under_test",
        PROXY_PATH,
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


class _Clock:
    def __init__(self) -> None:
        self.index = 0

    def wall(self) -> str:
        self.index += 1
        return f"2026-08-09T08:00:{self.index:02d}.000000Z"

    def monotonic_ns(self) -> int:
        return self.index * 1_000_000


def test_proxy_readiness_is_durable_and_bound_to_request(
    proxy_module: ModuleType,
    tmp_path: Path,
) -> None:
    request_path = tmp_path / "request.json"
    ready_path = tmp_path / "proxy-ready.json"
    receipt_path = tmp_path / "proxy-receipt.json"
    request_path.write_bytes(
        canonical_json_bytes(
            {
                "request_id": REQUEST_ID,
                "symbol": "000403.SZ",
            }
        )
    )
    request_path.chmod(0o444)
    clock = _Clock()
    recorder = proxy_module.ProviderStageRecorder(
        wall_clock=clock.wall,
        monotonic_ns=clock.monotonic_ns,
    )

    marker = proxy_module.publish_proxy_readiness(
        request_path=str(request_path),
        ready_path=str(ready_path),
        receipt_path=str(receipt_path),
        stage_recorder=recorder,
    )

    assert marker == {
        "request_id": REQUEST_ID,
        "proxy_ready": True,
        "wall_time": marker["wall_time"],
        "monotonic_ns": marker["monotonic_ns"],
        "listener_ready": True,
    }
    assert json.loads(ready_path.read_bytes()) == marker
    receipt = json.loads(receipt_path.read_bytes())
    assert receipt["request_id"] == REQUEST_ID
    assert receipt["terminal_status"] == "PROXY_READY"
    assert receipt["provider_attempt_count"] == 0
    assert receipt["proxy_ready"]["occurred"] is True
    assert receipt["relay_request_received"]["occurred"] is False
    assert receipt["provider_connect_started"]["occurred"] is False
    assert not list(tmp_path.glob("*.tmp"))
    assert not list(tmp_path.glob("*.partial"))


def test_proxy_readiness_rejects_unbound_request_id(
    proxy_module: ModuleType,
    tmp_path: Path,
) -> None:
    request_path = tmp_path / "request.json"
    request_path.write_text('{"request_id":"short"}\n', encoding="utf-8")
    request_path.chmod(0o444)
    recorder = proxy_module.ProviderStageRecorder()

    with pytest.raises(proxy_module.ProxyError):
        proxy_module.publish_proxy_readiness(
            request_path=str(request_path),
            ready_path=str(tmp_path / "proxy-ready.json"),
            receipt_path=str(tmp_path / "proxy-receipt.json"),
            stage_recorder=recorder,
        )

    assert not (tmp_path / "proxy-ready.json").exists()
    assert not (tmp_path / "proxy-receipt.json").exists()


def test_proxy_records_relay_arrival_before_any_provider_stage(
    proxy_module: ModuleType,
    tmp_path: Path,
) -> None:
    request_path = tmp_path / "request.json"
    ready_path = tmp_path / "proxy-ready.json"
    receipt_path = tmp_path / "proxy-receipt.json"
    request_path.write_bytes(
        canonical_json_bytes(
            {
                "request_id": REQUEST_ID,
                "symbol": "000403.SZ",
            }
        )
    )
    request_path.chmod(0o444)
    recorder = proxy_module.ProviderStageRecorder()
    proxy_module.publish_proxy_readiness(
        request_path=str(request_path),
        ready_path=str(ready_path),
        receipt_path=str(receipt_path),
        stage_recorder=recorder,
    )

    _status, _body, receipt = proxy_module.process_local_request(
        method="GET",
        path="/v1/typed-claims",
        content_length=None,
        body=b"",
        contract={},
        receipt_path=str(receipt_path),
        stage_recorder=recorder,
        expected_request_id=REQUEST_ID,
    )

    assert receipt["request_id"] == REQUEST_ID
    assert receipt["proxy_ready"]["occurred"] is True
    assert receipt["relay_request_received"]["occurred"] is True
    assert receipt["provider_connect_started"]["occurred"] is False
    assert receipt["terminal_status"] == "METHOD_BLOCKED"


@pytest.mark.parametrize("provider_stage_reached", [False, True])
def test_proxy_lifecycle_exception_overwrites_ready_with_terminal_receipt(
    proxy_module: ModuleType,
    tmp_path: Path,
    provider_stage_reached: bool,
) -> None:
    request_path = tmp_path / "request.json"
    ready_path = tmp_path / "proxy-ready.json"
    receipt_path = tmp_path / "proxy-receipt.json"
    request_path.write_bytes(
        canonical_json_bytes({"request_id": REQUEST_ID, "symbol": "000403.SZ"})
    )
    sensitive_error = "Authorization: Bearer lifecycle-secret"

    class FailingServer:
        def __init__(self, recorder: object) -> None:
            self.recorder = recorder
            self.closed = False

        def serve_forever(self) -> None:
            if provider_stage_reached:
                self.recorder.record("provider_connect_started")
            raise RuntimeError(sensitive_error)

        def server_close(self) -> None:
            self.closed = True

    servers: list[FailingServer] = []

    def server_factory(**kwargs: object) -> FailingServer:
        server = FailingServer(kwargs["stage_recorder"])
        servers.append(server)
        return server

    result = proxy_module.run_proxy_lifecycle(
        contract_loader=lambda: {},
        request_path=str(request_path),
        ready_path=str(ready_path),
        receipt_path=str(receipt_path),
        server_factory=server_factory,
    )

    receipt = json.loads(receipt_path.read_bytes())
    persisted = receipt_path.read_text(encoding="utf-8")
    assert result == 2
    assert servers[0].closed is True
    assert receipt["terminal_status"] == "PROXY_PROCESS_ERROR"
    assert receipt["response_category"] == "PROXY_PROCESS_ERROR"
    assert receipt["provider_attempt_count"] == int(provider_stage_reached)
    assert receipt["proxy_ready"]["occurred"] is True
    assert sensitive_error not in persisted
    assert "Authorization" not in persisted
    assert "Bearer " not in persisted


def test_proxy_lifecycle_does_not_swallow_base_exception(
    proxy_module: ModuleType,
    tmp_path: Path,
) -> None:
    request_path = tmp_path / "request.json"
    ready_path = tmp_path / "proxy-ready.json"
    receipt_path = tmp_path / "proxy-receipt.json"
    request_path.write_bytes(
        canonical_json_bytes({"request_id": REQUEST_ID, "symbol": "000403.SZ"})
    )

    class InterruptedServer:
        def serve_forever(self) -> None:
            raise KeyboardInterrupt

        def server_close(self) -> None:
            pass

    with pytest.raises(KeyboardInterrupt):
        proxy_module.run_proxy_lifecycle(
            contract_loader=lambda: {},
            request_path=str(request_path),
            ready_path=str(ready_path),
            receipt_path=str(receipt_path),
            server_factory=lambda **_kwargs: InterruptedServer(),
        )

    assert json.loads(receipt_path.read_bytes())["terminal_status"] == (
        "PROXY_READY"
    )


def test_proxy_lifecycle_overwrites_partial_ready_receipt_when_marker_fails(
    proxy_module: ModuleType,
    tmp_path: Path,
) -> None:
    request_path = tmp_path / "request.json"
    ready_path = tmp_path / "proxy-ready.json"
    receipt_path = tmp_path / "proxy-receipt.json"
    request_path.write_bytes(
        canonical_json_bytes({"request_id": REQUEST_ID, "symbol": "000403.SZ"})
    )

    class Server:
        def serve_forever(self) -> None:
            raise AssertionError("serve must not start after readiness failure")

        def server_close(self) -> None:
            pass

    def partial_readiness(**kwargs: object) -> dict[str, object]:
        recorder = kwargs["stage_recorder"]
        recorder.record("proxy_ready")
        proxy_module.write_receipt(
            proxy_module.build_receipt(
                request_id=REQUEST_ID,
                response_category="PROXY_READY",
                terminal_status="PROXY_READY",
                provider_attempt_count=0,
                stage_recorder=recorder,
            ),
            str(receipt_path),
        )
        raise proxy_module.ProxyError("UPSTREAM_REJECTED")

    result = proxy_module.run_proxy_lifecycle(
        contract_loader=lambda: {},
        request_path=str(request_path),
        ready_path=str(ready_path),
        receipt_path=str(receipt_path),
        server_factory=lambda **_kwargs: Server(),
        readiness_publisher=partial_readiness,
    )

    receipt = json.loads(receipt_path.read_bytes())
    assert result == 2
    assert receipt["terminal_status"] == "PROXY_READINESS_FAILED"
    assert receipt["response_category"] == "PROXY_READINESS_FAILED"
    assert receipt["provider_attempt_count"] == 0
    assert receipt["proxy_ready"]["occurred"] is True
    assert not ready_path.exists()
