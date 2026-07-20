from __future__ import annotations

import hashlib
import json
import logging
import stat
from datetime import datetime

import pytest

from app.services import gold_legacy_import
from app.services.gold_legacy_import import GoldImportError, GoldLegacyImporter
from app.services.gold_shadow_compare import LegacySample
from app.services.gold_shadow_store import GoldShadowStorageReadError, GoldShadowStore


def fixture_bytes(*, minute: int = 0) -> bytes:
    return (
        f"2026-07-16 15:{minute:02d}:00 INFO price=19.99 P=-12.59 V=-0.13 A=-0.93 "
        "state=恐慌 api_key=legacy-secret\n"
        f"2026-07-16 15:{minute:02d}:01 WARNING [ALERT] 恐慌极端\n"
    ).encode()


def downgrade_import_metadata(store: GoldShadowStore) -> tuple[dict[str, object], str]:
    index_path = store.root / "import_index.jsonl"
    metadata = json.loads(index_path.read_text(encoding="utf-8"))
    del metadata["normalized_sha256"]
    serialized = json.dumps(metadata, ensure_ascii=False, separators=(",", ":")) + "\n"
    index_path.write_text(serialized, encoding="utf-8")
    return metadata, serialized


def normalized_row(*, minute: int = 0) -> dict[str, object]:
    return {
        "schema_version": 1,
        "observed_at": f"2026-07-16T14:{minute:02d}:00+08:00",
        "price": 19.99,
        "P": -12.59,
        "V": -0.13,
        "A": -0.93,
        "state": "恐慌",
        "signals": ["恐慌极端"],
    }


def test_import_normalizes_and_discards_raw_text(tmp_path):
    importer = GoldLegacyImporter(GoldShadowStore(tmp_path))

    result = importer.import_bytes("monitor.log", fixture_bytes())

    path = tmp_path / "user_data/gold_shadow/imports" / f"{result.import_id}.jsonl"
    persisted = path.read_text(encoding="utf-8")
    row = json.loads(persisted)
    assert result.sample_count == 1
    assert not hasattr(result, "normalized_sha256")
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


def test_txt_extension_uses_legacy_text_parser(tmp_path):
    result = GoldLegacyImporter(GoldShadowStore(tmp_path)).import_bytes(
        "MONITOR.TXT", fixture_bytes()
    )

    assert result.filename == "MONITOR.TXT"
    assert result.sample_count == 1


@pytest.mark.parametrize("extension", ["json", "JSON"])
def test_json_import_requires_array_of_exact_normalized_rows(tmp_path, extension):
    store = GoldShadowStore(tmp_path)
    rows = [normalized_row(minute=55), normalized_row(minute=56)]
    payload = json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode()

    result = GoldLegacyImporter(store).import_bytes(f"../../samples.{extension}", payload)

    assert result.filename == f"samples.{extension}"
    assert result.sample_count == 2
    assert store.get_import(result.import_id)["rows"] == rows


def test_jsonl_import_accepts_one_exact_object_per_nonempty_line(tmp_path):
    store = GoldShadowStore(tmp_path)
    rows = [normalized_row(minute=55), normalized_row(minute=56)]
    payload = (
        json.dumps(rows[0], ensure_ascii=False)
        + "\n\n"
        + json.dumps(rows[1], ensure_ascii=False)
        + "\n"
    ).encode()

    result = GoldLegacyImporter(store).import_bytes("samples.JsOnL", payload)

    assert result.sample_count == 2
    assert store.get_import(result.import_id)["rows"] == rows


def test_import_rejects_unsupported_extension_before_parsing(tmp_path, monkeypatch):
    def unexpected_parse(_text):
        pytest.fail("unsupported upload was parsed")

    monkeypatch.setattr(gold_legacy_import, "parse_legacy_text", unexpected_parse)
    store = GoldShadowStore(tmp_path)

    with pytest.raises(GoldImportError, match=r"^invalid_extension$"):
        GoldLegacyImporter(store).import_bytes("../../monitor.csv", fixture_bytes())

    assert not store.root.exists()


@pytest.mark.parametrize(
    ("filename", "payload", "code"),
    [
        ("rows.json", b"{}", "invalid_json_structure"),
        ("rows.json", b"[", "invalid_json"),
        ("rows.jsonl", b"[]\n", "invalid_json_structure"),
        ("rows.jsonl", b"{\n", "invalid_json"),
        ("rows.json", b"[]", "no_valid_samples"),
        ("rows.jsonl", b"\n \n", "no_valid_samples"),
    ],
)
def test_json_formats_reject_invalid_or_empty_structures(tmp_path, filename, payload, code):
    store = GoldShadowStore(tmp_path)

    with pytest.raises(GoldImportError, match=rf"^{code}$"):
        GoldLegacyImporter(store).import_bytes(filename, payload)

    assert not (store.root / "import_index.jsonl").exists()
    assert not (store.root / "imports").exists()


@pytest.mark.parametrize("filename", ["rows.json", "rows.jsonl"])
def test_json_formats_reject_extra_source_fields_without_partial_writes(tmp_path, filename):
    store = GoldShadowStore(tmp_path)
    rows = [normalized_row(minute=55), {**normalized_row(minute=56), "raw": "secret"}]
    if filename.endswith(".jsonl"):
        payload = "\n".join(json.dumps(row, ensure_ascii=False) for row in rows).encode()
    else:
        payload = json.dumps(rows, ensure_ascii=False).encode()

    with pytest.raises(GoldImportError, match=r"^invalid_sample$") as caught:
        GoldLegacyImporter(store).import_bytes(filename, payload)

    assert "secret" not in str(caught.value)
    assert not (store.root / "import_index.jsonl").exists()
    assert not (store.root / "imports").exists()


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


def test_get_import_rejects_schema_valid_normalized_row_tampering(tmp_path):
    store = GoldShadowStore(tmp_path)
    result = GoldLegacyImporter(store).import_bytes("monitor.log", fixture_bytes())
    path = store.root / "imports" / f"{result.import_id}.jsonl"
    row = json.loads(path.read_text(encoding="utf-8"))
    row["price"] = 20.00
    path.write_text(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")

    with pytest.raises(GoldShadowStorageReadError, match="unable to read import"):
        store.get_import(result.import_id)


def test_legacy_index_requires_manual_reimport_without_rewriting_metadata(tmp_path):
    store = GoldShadowStore(tmp_path)
    result = GoldLegacyImporter(store).import_bytes("monitor.log", fixture_bytes())
    index_path = store.root / "import_index.jsonl"
    legacy_metadata = json.loads(index_path.read_text(encoding="utf-8"))
    del legacy_metadata["normalized_sha256"]
    legacy_index = json.dumps(legacy_metadata, ensure_ascii=False) + "\n"
    index_path.write_text(legacy_index, encoding="utf-8")

    with pytest.raises(
        GoldShadowStorageReadError,
        match=r"^legacy_import_requires_manual_reimport_from_original_bytes$",
    ):
        GoldShadowStore(tmp_path).get_import(result.import_id)

    assert index_path.read_text(encoding="utf-8") == legacy_index


def test_tampered_legacy_index_requires_manual_reimport_without_rewriting_metadata(tmp_path):
    store = GoldShadowStore(tmp_path)
    result = GoldLegacyImporter(store).import_bytes("monitor.log", fixture_bytes())
    index_path = store.root / "import_index.jsonl"
    legacy_metadata = json.loads(index_path.read_text(encoding="utf-8"))
    del legacy_metadata["normalized_sha256"]
    legacy_index = json.dumps(legacy_metadata, ensure_ascii=False) + "\n"
    index_path.write_text(legacy_index, encoding="utf-8")
    import_path = store.root / "imports" / f"{result.import_id}.jsonl"
    row = json.loads(import_path.read_text(encoding="utf-8"))
    row["price"] = 20.00
    import_path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")

    with pytest.raises(
        GoldShadowStorageReadError,
        match=r"^legacy_import_requires_manual_reimport_from_original_bytes$",
    ):
        GoldShadowStore(tmp_path).get_import(result.import_id)

    assert index_path.read_text(encoding="utf-8") == legacy_index


def test_reupload_repairs_legacy_import_from_original_bytes_without_trusting_normalized_file(
    tmp_path,
):
    store = GoldShadowStore(tmp_path)
    importer = GoldLegacyImporter(store)
    original = importer.import_bytes("monitor.log", fixture_bytes())
    legacy_metadata, _ = downgrade_import_metadata(store)
    import_path = store.root / "imports" / f"{original.import_id}.jsonl"
    tampered = json.loads(import_path.read_text(encoding="utf-8"))
    tampered["price"] = 999.0
    import_path.write_text(
        json.dumps(tampered, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )

    repaired = importer.import_bytes("../../repair.LOG", fixture_bytes())

    metadata = store.list_imports(10)[0]
    persisted = json.loads(import_path.read_text(encoding="utf-8"))
    assert repaired.import_id == original.import_id
    assert repaired.filename == "repair.LOG"
    assert repaired.sample_count == 1
    assert metadata == {
        **legacy_metadata,
        "normalized_sha256": metadata["normalized_sha256"],
        "filename": "repair.LOG",
        "sample_count": 1,
    }
    assert persisted["price"] == 19.99
    assert "normalized_sha256" in metadata


def test_legacy_repair_rejects_different_original_bytes_without_writes(tmp_path):
    store = GoldShadowStore(tmp_path)
    importer = GoldLegacyImporter(store)
    original = importer.import_bytes("monitor.log", fixture_bytes())
    _, legacy_index = downgrade_import_metadata(store)
    import_path = store.root / "imports" / f"{original.import_id}.jsonl"
    legacy_rows = import_path.read_bytes()

    with pytest.raises(GoldImportError, match=r"^legacy_import_original_mismatch$"):
        importer.import_bytes("wrong.log", fixture_bytes(minute=5))

    assert (store.root / "import_index.jsonl").read_text(encoding="utf-8") == legacy_index
    assert import_path.read_bytes() == legacy_rows
    assert len(list((store.root / "imports").glob("*.jsonl"))) == 1


def test_legacy_repair_rejects_nonmatching_digest_before_decoding(
    tmp_path, monkeypatch
):
    store = GoldShadowStore(tmp_path)
    importer = GoldLegacyImporter(store)
    importer.import_bytes("monitor.log", fixture_bytes())
    downgrade_import_metadata(store)

    def unexpected_parse(_text):
        pytest.fail("nonmatching repair bytes were decoded and parsed")

    monkeypatch.setattr(gold_legacy_import, "parse_legacy_text", unexpected_parse)

    with pytest.raises(GoldImportError, match=r"^legacy_import_original_mismatch$"):
        importer.import_bytes("wrong.log", b"\xff")


def test_legacy_repair_data_write_interruption_is_atomic_and_retryable(tmp_path, monkeypatch):
    store = GoldShadowStore(tmp_path)
    importer = GoldLegacyImporter(store)
    original = importer.import_bytes("monitor.log", fixture_bytes())
    _, legacy_index = downgrade_import_metadata(store)
    import_path = store.root / "imports" / f"{original.import_id}.jsonl"
    previous_rows = import_path.read_bytes()

    import app.services.gold_shadow_store as store_module

    real_replace = store_module.os.replace

    def fail_data_replace(source, target, *args, **kwargs):
        if target == import_path.name:
            raise OSError("injected repair data failure")
        return real_replace(source, target, *args, **kwargs)

    monkeypatch.setattr(store_module.os, "replace", fail_data_replace)

    with pytest.raises(OSError, match="injected repair data failure"):
        importer.import_bytes("repair.log", fixture_bytes())

    assert (store.root / "import_index.jsonl").read_text(encoding="utf-8") == legacy_index
    assert import_path.read_bytes() == previous_rows
    assert list((store.root / "imports").glob("*.tmp")) == []

    monkeypatch.setattr(store_module.os, "replace", real_replace)
    assert importer.import_bytes("repair.log", fixture_bytes()).import_id == original.import_id


def test_legacy_repair_index_write_interruption_commits_data_first_and_is_retryable(
    tmp_path, monkeypatch
):
    store = GoldShadowStore(tmp_path)
    importer = GoldLegacyImporter(store)
    original = importer.import_bytes("monitor.log", fixture_bytes())
    _, legacy_index = downgrade_import_metadata(store)
    import_path = store.root / "imports" / f"{original.import_id}.jsonl"
    tampered = json.loads(import_path.read_text(encoding="utf-8"))
    tampered["price"] = 999.0
    import_path.write_text(json.dumps(tampered, ensure_ascii=False) + "\n", encoding="utf-8")
    original_replace = store._replace_jsonl_locked

    def fail_index_replace(filename, rows):
        if filename == "import_index.jsonl":
            raise OSError("injected repair index failure")
        return original_replace(filename, rows)

    monkeypatch.setattr(store, "_replace_jsonl_locked", fail_index_replace)

    with pytest.raises(OSError, match="injected repair index failure"):
        importer.import_bytes("repair.log", fixture_bytes())

    assert (store.root / "import_index.jsonl").read_text(encoding="utf-8") == legacy_index
    assert json.loads(import_path.read_text(encoding="utf-8"))["price"] == 19.99

    monkeypatch.setattr(store, "_replace_jsonl_locked", original_replace)
    assert importer.import_bytes("repair.log", fixture_bytes()).import_id == original.import_id


def test_repaired_reupload_is_idempotent_and_verifies_normalized_digest(tmp_path):
    store = GoldShadowStore(tmp_path)
    importer = GoldLegacyImporter(store)
    importer.import_bytes("monitor.log", fixture_bytes())
    downgrade_import_metadata(store)
    repaired = importer.import_bytes("repair.log", fixture_bytes())
    index_path = store.root / "import_index.jsonl"
    repaired_index = index_path.read_bytes()

    duplicate = importer.import_bytes("ignored.log", fixture_bytes())

    assert duplicate == repaired
    assert index_path.read_bytes() == repaired_index
    assert len(store.list_imports(10)) == 1
    assert len(list((store.root / "imports").glob("*.jsonl"))) == 1


def test_find_import_by_sha256_rejects_tampered_duplicate_backing_file(tmp_path):
    store = GoldShadowStore(tmp_path)
    result = GoldLegacyImporter(store).import_bytes("monitor.log", fixture_bytes())
    path = store.root / "imports" / f"{result.import_id}.jsonl"
    row = json.loads(path.read_text(encoding="utf-8"))
    row["price"] = 20.00
    path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")

    with pytest.raises(GoldShadowStorageReadError, match="unable to read import"):
        store.find_import_by_sha256(result.sha256)


def test_commit_import_duplicate_rejects_tampered_backing_file(tmp_path):
    store = GoldShadowStore(tmp_path)
    result = GoldLegacyImporter(store).import_bytes("monitor.log", fixture_bytes())
    path = store.root / "imports" / f"{result.import_id}.jsonl"
    row = json.loads(path.read_text(encoding="utf-8"))
    row["price"] = 20.00
    path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
    original = {**row, "price": 19.99}

    with pytest.raises(GoldShadowStorageReadError, match="unable to read import"):
        store.commit_import(result.sha256, "duplicate.log", [original])


def test_importer_duplicate_rejects_tampered_backing_file(tmp_path):
    store = GoldShadowStore(tmp_path)
    importer = GoldLegacyImporter(store)
    result = importer.import_bytes("monitor.log", fixture_bytes())
    path = store.root / "imports" / f"{result.import_id}.jsonl"
    row = json.loads(path.read_text(encoding="utf-8"))
    row["price"] = 20.00
    path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")

    with pytest.raises(GoldShadowStorageReadError, match="unable to read import"):
        importer.import_bytes("duplicate.log", fixture_bytes())


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


def test_importer_rejects_non_vocabulary_signal_before_persistence(tmp_path, monkeypatch):
    store = GoldShadowStore(tmp_path)
    sample = LegacySample(
        observed_at=datetime.fromisoformat("2026-07-16T15:00:00+08:00"),
        price=19.99,
        p=-12.59,
        v=-0.13,
        a=-0.93,
        state="恐慌",
        signals=("api_key=source-line-secret",),
        source_line=1,
    )
    monkeypatch.setattr(gold_legacy_import, "parse_legacy_text", lambda _text: [sample])

    with pytest.raises(GoldImportError, match=r"^invalid_sample$"):
        GoldLegacyImporter(store).import_bytes("monitor.log", b"synthetic valid UTF-8")

    assert not (store.root / "import_index.jsonl").exists()
    assert not (store.root / "imports").exists()


def test_store_rejects_non_vocabulary_signal_before_persistence(tmp_path):
    store = GoldShadowStore(tmp_path)
    digest = "b" * 64
    row = {
        "schema_version": 1,
        "observed_at": "2026-07-16T15:00:00+08:00",
        "price": 19.99,
        "P": -12.59,
        "V": -0.13,
        "A": -0.93,
        "state": "恐慌",
        "signals": ["api_key=source-line-secret"],
    }

    with pytest.raises(ValueError, match="import signal is invalid"):
        store.commit_import(digest, "monitor.log", [row])

    assert not (store.root / "import_index.jsonl").exists()
    assert not (store.root / "imports" / f"{digest}.jsonl").exists()


def test_data_commit_failure_leaves_no_index_or_partial_import(tmp_path, monkeypatch):
    store = GoldShadowStore(tmp_path)
    importer = GoldLegacyImporter(store)
    digest = hashlib.sha256(fixture_bytes()).hexdigest()

    import app.services.gold_shadow_store as store_module

    original_replace = store_module.os.replace

    def fail_data_replace(source, target, *args, **kwargs):
        if target == f"{digest}.jsonl":
            raise OSError("injected data commit failure")
        return original_replace(source, target, *args, **kwargs)

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
