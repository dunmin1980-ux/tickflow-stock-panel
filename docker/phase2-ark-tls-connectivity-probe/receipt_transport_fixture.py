"""Local-only receipt publication fixture; it never executes the TLS probe."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

PROBE_SOURCE = Path("/probe/probe.py")
MODES = {
    "happy",
    "permission_denied",
    "wrong_destination",
    "rename_fail",
    "missing_ready",
    "malformed",
}


def _load_probe() -> ModuleType:
    spec = importlib.util.spec_from_file_location("receipt_transport_probe", PROBE_SOURCE)
    if spec is None or spec.loader is None:
        raise RuntimeError("fixture_probe_import_failed")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _valid_local_receipt(probe: ModuleType, probe_id: str) -> dict[str, Any]:
    counter = iter(range(1, 100))
    recorder = probe.ProbeEventRecorder(
        wall_clock=lambda: "2026-08-14T00:00:00Z",
        monotonic_ns=lambda: next(counter),
    )
    for name in probe.CHILD_SUCCESS_EVENTS:
        recorder.record(name)
    receipt = probe._base_receipt(probe_id, recorder)
    receipt.update(
        {
            "terminal_status": "PROBE_PASSED",
            "tls_error_category": "NONE",
            "tls_version": "LOCAL_ONLY",
            "cipher_name": "LOCAL_ONLY",
            "san_contains_hostname": True,
            "events": recorder.values(),
        }
    )
    probe.validate_probe_receipt(receipt, require_cleanup=False)
    return receipt


def _emit_publication_error(probe: ModuleType, error: BaseException) -> int:
    category = (
        error.category
        if isinstance(error, probe.ReceiptPublicationError)
        else "RECEIPT_VALIDATION_FAILED"
    )
    sys.stderr.write(
        json.dumps(
            {
                "category": category,
                "receipt_transport_schema_version": 1,
            },
            sort_keys=True,
        )
        + "\n"
    )
    return 70


def run(mode: str) -> int:
    if mode not in MODES:
        return 64
    probe = _load_probe()
    probe_id = probe._read_probe_id()
    receipt = _valid_local_receipt(probe, probe_id)
    try:
        if mode == "happy":
            probe.publish_receipt_bundle(receipt)
        elif mode == "permission_denied":
            probe.publish_receipt_bundle(receipt)
        elif mode == "wrong_destination":
            destination = probe.OUTPUT_PATH.parent / "wrong-destination"
            destination.mkdir(mode=0o700)
            probe.publish_receipt_bundle(receipt, output_dir=destination)
        elif mode == "rename_fail":
            def deny_rename(_source: Any, _destination: Any) -> None:
                raise OSError("fixture rename denied")

            probe.os.replace = deny_rename
            probe.publish_receipt_bundle(receipt)
        elif mode == "missing_ready":
            probe._durable_publish_json(probe.OUTPUT_PATH, receipt)
        elif mode == "malformed":
            marker = probe.publish_receipt_bundle(receipt)
            malformed = b"{\n"
            descriptor = os.open(probe.OUTPUT_PATH, os.O_WRONLY | os.O_TRUNC)
            try:
                os.write(descriptor, malformed)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            if marker["receipt_sha256"] == hashlib.sha256(malformed).hexdigest():
                raise RuntimeError("fixture_malformed_hash_collision")
    except BaseException as error:
        return _emit_publication_error(probe, error)
    return 0


def main() -> int:
    if len(sys.argv) != 2:
        return 64
    return run(sys.argv[1])


if __name__ == "__main__":
    raise SystemExit(main())
