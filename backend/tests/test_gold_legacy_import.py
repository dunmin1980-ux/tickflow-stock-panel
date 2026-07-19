from __future__ import annotations

import hashlib
import json
import logging
import stat

import pytest

from app.services import gold_legacy_import
from app.services.gold_legacy_import import GoldImportError, GoldLegacyImporter
from app.services.gold_shadow_store import GoldShadowStore


def fixture_bytes(*, minute: int = 0) -> bytes:
    return (
        f"2026-07-16 15:{minute:02d}:00 INFO price=19.99 P=-12.59 V=-0.13 A=-0.93 "
        "state=恐慌 api_key=legacy-secret\n"
        f"2026-07-16 15:{minute:02d}:01 WARNING [ALERT] 恐慌极端\n"
    ).encode()


def test_import_normalizes_and_discards_raw_text(tmp_path):
    importer = GoldLegacyImporter(GoldShadowStore(tmp_path))

    result = importer.import_bytes("monitor.log", fixture_bytes())

    path = tmp_path / "user_data/gold_shadow/imports" / f"{result.import_id}.jsonl"
    persisted = path.read_text(encoding="utf-8")
    row = json.loads(persisted)
    assert result.sample_count == 1
    assert "[ALERT]" not in persisted
    assert "legacy-secret" not in persisted
    assert set(row) == {
        "schema_version",
        "observed_at",
        "price",
        "P",
        "V",
        "A",
        "state",
        "signals",
    }
    assert row == {
        "schema_version": 1,
        "observed_at": "2026-07-16T15:00:00+08:00",
        "price": 19.99,
        "P": -12.59,
        "V": -0.13,
        "A": -0.93,
        "state": "恐慌",
        "signals": ["恐慌极端"],
    }


def test_import_rejects_oversize_before_parsing(tmp_path, monkeypatch):
    def unexpected_parse(_text):
        pytest.fail("oversize payload was parsed")

    monkeypatch.setattr(gold_legacy_import, "parse_legacy_text", unexpected_parse)
    importer = GoldLegacyImporter(GoldShadowStore(tmp_path))

    with pytest.raises(GoldImportError, match=r"^upload_too_large$"):
        importer.import_bytes("monitor.log", b"x" * (10 * 1024 * 1024 + 1))


@pytest.mark.parametrize("magic", [b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"])
def test_import_rejects_zip_magic_before_parsing(tmp_path, monkeypatch, magic):
    def unexpected_parse(_text):
        pytest.fail("ZIP payload was parsed")

    monkeypatch.setattr(gold_legacy_import, "parse_legacy_text", unexpected_parse)
    importer = GoldLegacyImporter(GoldShadowStore(tmp_path))

    with pytest.raises(GoldImportError, match=r"^archive_not_allowed$"):
        importer.import_bytes("monitor.log", magic + b"not-a-log")


def test_import_rejects_invalid_utf8(tmp_path):
    importer = GoldLegacyImporter(GoldShadowStore(tmp_path))

    with pytest.raises(GoldImportError, match=r"^invalid_utf8$"):
        importer.import_bytes("monitor.log", b"\xff\xfelegacy-secret")


def test_import_rejects_no_valid_samples_without_exposing_source_lines(tmp_path, caplog):
    importer = GoldLegacyImporter(GoldShadowStore(tmp_path))
    payload = b"api_key=source-line-secret P=-1 V=0 A=0 price=20\n"

    with caplog.at_level(logging.WARNING), pytest.raises(GoldImportError) as caught:
        importer.import_bytes("monitor.log", payload)

    assert str(caught.value) == "no_valid_samples"
    assert "source-line-secret" not in str(caught.value)
    assert "source-line-secret" not in caplog.text


@pytest.mark.parametrize(
    ("unsafe", "expected"),
    [
        ("../../private/monitor.log", "monitor.log"),
        (r"C:\\private\\monitor.log", "monitor.log"),
    ],
)
def test_import_normalizes_unsafe_filename_to_basename(tmp_path, unsafe, expected):
    result = GoldLegacyImporter(GoldShadowStore(tmp_path)).import_bytes(unsafe, fixture_bytes())

    assert result.filename == expected
    assert GoldShadowStore(tmp_path).list_imports(1)[0]["filename"] == expected


def test_duplicate_sha256_returns_same_id_without_duplicate_persistence(tmp_path):
    store = GoldShadowStore(tmp_path)
    importer = GoldLegacyImporter(store)
    payload = fixture_bytes()

    first = importer.import_bytes("first.log", payload)
    second = importer.import_bytes("second.log", payload)

    assert second == first
    assert first.import_id == hashlib.sha256(payload).hexdigest()
    assert len(store.list_imports(10)) == 1
    assert len((store.root / "import_index.jsonl").read_text(encoding="utf-8").splitlines()) == 1
    assert len(list((store.root / "imports").glob("*.jsonl"))) == 1


def test_store_gets_normalized_rows_and_lists_newest_first(tmp_path):
    store = GoldShadowStore(tmp_path)
    importer = GoldLegacyImporter(store)
    first = importer.import_bytes("first.log", fixture_bytes())
    second = importer.import_bytes("second.log", fixture_bytes(minute=5))

    assert [row["import_id"] for row in store.list_imports(10)] == [
        second.import_id,
        first.import_id,
    ]
    assert store.list_imports(0) == []
    imported = store.get_import(first.import_id)
    assert imported is not None
    assert imported["import_id"] == first.import_id
    assert imported["rows"][0]["observed_at"] == "2026-07-16T15:00:00+08:00"
    assert store.get_import("../unsafe") is None


def test_import_storage_is_private(tmp_path):
    store = GoldShadowStore(tmp_path)
    GoldLegacyImporter(store).import_bytes("monitor.log", fixture_bytes())

    assert stat.S_IMODE(store.root.stat().st_mode) == 0o700
    assert stat.S_IMODE((store.root / "imports").stat().st_mode) == 0o700
    assert stat.S_IMODE((store.root / "import_index.jsonl").stat().st_mode) == 0o600
    import_path = next((store.root / "imports").glob("*.jsonl"))
    assert stat.S_IMODE(import_path.stat().st_mode) == 0o600


def test_store_rejects_non_normalized_import_rows_before_writing(tmp_path):
    store = GoldShadowStore(tmp_path)
    digest = "a" * 64
    unsafe_row = {
        "schema_version": 1,
        "observed_at": "2026-07-16T15:00:00+08:00",
        "price": 19.99,
        "P": -12.59,
        "V": -0.13,
        "A": -0.93,
        "state": "恐慌",
        "signals": [],
        "source_line": "api_key=source-line-secret",
    }

    with pytest.raises(ValueError, match="normalized import fields"):
        store.commit_import(digest, "monitor.log", [unsafe_row])

    assert not (store.root / "import_index.jsonl").exists()
    assert not (store.root / "imports" / f"{digest}.jsonl").exists()


def test_data_commit_failure_leaves_no_index_or_partial_import(tmp_path, monkeypatch):
    store = GoldShadowStore(tmp_path)
    importer = GoldLegacyImporter(store)
    digest = hashlib.sha256(fixture_bytes()).hexdigest()

    import app.services.gold_shadow_store as store_module

    original_replace = store_module.os.replace

    def fail_data_replace(source, target):
        if target == store.root / "imports" / f"{digest}.jsonl":
            raise OSError("injected data commit failure")
        return original_replace(source, target)

    monkeypatch.setattr(store_module.os, "replace", fail_data_replace)

    with pytest.raises(OSError, match="injected data commit failure"):
        importer.import_bytes("monitor.log", fixture_bytes())

    assert not (store.root / "import_index.jsonl").exists()
    assert not (store.root / "imports" / f"{digest}.jsonl").exists()
    assert list((store.root / "imports").glob("*.tmp")) == []


def test_index_commit_failure_preserves_existing_index_without_dangling_entry(
    tmp_path, monkeypatch
):
    store = GoldShadowStore(tmp_path)
    importer = GoldLegacyImporter(store)
    first = importer.import_bytes("first.log", fixture_bytes())
    second_payload = fixture_bytes(minute=5)
    second_digest = hashlib.sha256(second_payload).hexdigest()
    original_replace = store._replace_jsonl_locked

    def fail_index_replace(filename, rows):
        if filename == "import_index.jsonl":
            raise OSError("injected index commit failure")
        return original_replace(filename, rows)

    monkeypatch.setattr(store, "_replace_jsonl_locked", fail_index_replace)

    with pytest.raises(OSError, match="injected index commit failure"):
        importer.import_bytes("second.log", second_payload)

    assert [row["import_id"] for row in GoldShadowStore(tmp_path).list_imports(10)] == [
        first.import_id
    ]
    assert (store.root / "imports" / f"{second_digest}.jsonl").exists()
