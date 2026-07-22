#!/usr/bin/env python3
"""Compute a path-stable SHA-256 digest for a directory artifact."""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path


def _entries(root: Path) -> list[Path]:
    entries: list[Path] = []
    for current, dirnames, filenames in os.walk(root, followlinks=False):
        current_path = Path(current)
        entries.extend(current_path / name for name in dirnames)
        entries.extend(current_path / name for name in filenames)
    return sorted(entries, key=lambda path: path.relative_to(root).as_posix())


def _field(digest: hashlib._Hash, value: bytes) -> None:
    digest.update(len(value).to_bytes(8, "big"))
    digest.update(value)


def tree_sha256(root: Path) -> str:
    root = Path(root)
    if not root.is_dir():
        raise ValueError(f"artifact tree is not a directory: {root}")

    digest = hashlib.sha256(b"tickflow-artifact-tree-v1\0")
    for path in _entries(root):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        if path.is_symlink():
            kind = b"L"
            payload = os.readlink(path).encode("utf-8")
        elif path.is_dir():
            kind = b"D"
            payload = b""
        elif path.is_file():
            kind = b"F"
            payload = b""
        else:
            raise ValueError(f"unsupported artifact entry: {path}")

        digest.update(kind)
        _field(digest, relative)
        if kind == b"F":
            _field(digest, str(path.stat().st_size).encode("ascii"))
            with path.open("rb") as handle:
                while chunk := handle.read(1024 * 1024):
                    digest.update(chunk)
        else:
            _field(digest, payload)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    print(tree_sha256(args.directory))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
