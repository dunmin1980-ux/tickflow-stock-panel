#!/usr/bin/env python3
"""Wait for the host topology gate before entering the immutable TLS runner."""

from __future__ import annotations

import argparse
import os
import stat
import time
from pathlib import Path

RUNNER = "/probe/probe.py"
OUTPUT = "/output/receipt.json"


def validate_dispatch_gate(path: Path, *, probe_id: str) -> None:
    expected = (probe_id + "\n").encode("ascii")
    flags = os.O_RDONLY | os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise ValueError("dispatch_gate_invalid") from error
    try:
        metadata = os.fstat(descriptor)
        raw = os.read(descriptor, len(expected) + 1)
    except OSError as error:
        raise ValueError("dispatch_gate_invalid") from error
    finally:
        os.close(descriptor)
    if not stat.S_ISREG(metadata.st_mode) or raw != expected:
        raise ValueError("dispatch_gate_invalid")


def wait_for_dispatch_gate(path: Path, *, probe_id: str) -> None:
    while not os.path.lexists(path):
        time.sleep(0.05)
    validate_dispatch_gate(path, probe_id=probe_id)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gate", type=Path, required=True)
    parser.add_argument("--probe-id", required=True)
    args = parser.parse_args()
    wait_for_dispatch_gate(args.gate, probe_id=args.probe_id)
    os.execv(
        "/usr/bin/python3",
        [
            "/usr/bin/python3",
            RUNNER,
            "--probe-id",
            args.probe_id,
            "--output",
            OUTPUT,
        ],
    )
    raise RuntimeError("dispatch_exec_returned")


if __name__ == "__main__":
    raise SystemExit(main())
