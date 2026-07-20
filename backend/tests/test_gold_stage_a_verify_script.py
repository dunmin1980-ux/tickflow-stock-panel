from pathlib import Path


def test_verify_script_has_no_file_fallback_after_compose_failure() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    script = (repo_root / "scripts" / "verify_gold_stage_a.sh").read_text(encoding="utf-8")

    assert "compose binary skipped" not in script
    assert "docker compose config --quiet" in script
