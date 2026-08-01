from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
from pathlib import Path
from types import ModuleType

from app.services.phase2_ai_worker_protocol import (
    WORKER_CANDIDATE_VALID,
    build_worker_projection,
    validate_worker_candidate,
)
from app.services.phase2_claims_service import canonical_json_bytes

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKER_PATH = REPO_ROOT / "docker/phase2-ai-worker/worker.py"
WORKER_CONTEXT = REPO_ROOT / "docker/phase2-ai-worker"


def _load_worker() -> ModuleType:
    assert WORKER_PATH.is_file(), "isolated fake worker module is missing"
    spec = importlib.util.spec_from_file_location(
        "phase2_isolated_fake_worker",
        WORKER_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _projection(symbol: str = "000403.SZ") -> dict:
    facts_path = (
        REPO_ROOT
        / "reports/phase2_facts"
        / f'{symbol.replace(".", "")}_facts.json'
    )
    facts_bytes = facts_path.read_bytes()
    facts = json.loads(facts_bytes)
    return build_worker_projection(
        facts,
        hashlib.sha256(facts_bytes).hexdigest(),
        facts_bytes=facts_bytes,
    )


def test_fake_provider_generates_complete_host_valid_candidate() -> None:
    worker = _load_worker()
    projection = _projection()

    candidate = worker.generate_candidate(projection)
    validation = validate_worker_candidate(candidate, projection)

    assert validation.status == WORKER_CANDIDATE_VALID
    assert validation.errors == []
    assert validation.claim_count == 42
    assert candidate["symbol"] == "000403.SZ"
    assert candidate["trading_advice"] is False


def test_fake_provider_generation_is_byte_deterministic() -> None:
    worker = _load_worker()
    projection = _projection()

    first = canonical_json_bytes(worker.generate_candidate(projection))
    second = canonical_json_bytes(worker.generate_candidate(projection))

    assert first == second
    assert hashlib.sha256(first).hexdigest() == hashlib.sha256(second).hexdigest()


def test_worker_runtime_has_a_standard_library_only_dependency_surface() -> None:
    assert WORKER_PATH.is_file(), "isolated fake worker module is missing"
    tree = ast.parse(WORKER_PATH.read_text(encoding="utf-8"))
    imported_roots = {
        alias.name.split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported_roots.update(
        node.module.split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    )

    assert imported_roots <= {
        "__future__",
        "collections",
        "errno",
        "hashlib",
        "itertools",
        "json",
        "math",
        "os",
        "pathlib",
        "socket",
        "subprocess",
        "sys",
        "typing",
    }


def test_worker_mount_placeholders_are_small_regular_safe_files() -> None:
    expected = {
        "projection.placeholder.json": b"{}\n",
        "candidate.placeholder.json": b"\n",
    }

    for filename, content in expected.items():
        path = WORKER_CONTEXT / filename
        assert path.is_file() and not path.is_symlink()
        assert path.read_bytes() == content
        assert path.stat().st_size < 128
