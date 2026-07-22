from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_module():
    script = Path(__file__).parents[2] / "scripts" / "artifact_tree_digest.py"
    spec = importlib.util.spec_from_file_location("artifact_tree_digest", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_tree_digest_is_stable_across_mtime_and_creation_order(tmp_path: Path) -> None:
    module = _load_module()
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()

    (first / "nested").mkdir()
    (first / "nested" / "b.txt").write_text("bravo", encoding="utf-8")
    (first / "a.txt").write_text("alpha", encoding="utf-8")

    (second / "a.txt").write_text("alpha", encoding="utf-8")
    (second / "nested").mkdir()
    (second / "nested" / "b.txt").write_text("bravo", encoding="utf-8")
    (second / "a.txt").touch()

    assert module.tree_sha256(first) == module.tree_sha256(second)


def test_tree_digest_covers_file_paths_content_and_symlink_targets(tmp_path: Path) -> None:
    module = _load_module()
    root = tmp_path / "artifact"
    root.mkdir()
    payload = root / "payload.txt"
    payload.write_text("one", encoding="utf-8")
    link = root / "current"
    link.symlink_to("payload.txt")

    original = module.tree_sha256(root)
    payload.write_text("two", encoding="utf-8")
    assert module.tree_sha256(root) != original

    payload.write_text("one", encoding="utf-8")
    link.unlink()
    link.symlink_to("elsewhere.txt")
    assert module.tree_sha256(root) != original

    link.unlink()
    (root / "renamed.txt").write_text("one", encoding="utf-8")
    payload.unlink()
    assert module.tree_sha256(root) != original
