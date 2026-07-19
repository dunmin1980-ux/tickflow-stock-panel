import json
import os
import stat

import pytest

from app.services.gold_external_guard import (
    DisabledGoldNotifier,
    GoldExternalSendDenied,
    attempt_count,
)


def test_disabled_notifier_records_and_denies(tmp_path):
    notifier = DisabledGoldNotifier(tmp_path / "gold_shadow")

    with pytest.raises(GoldExternalSendDenied):
        notifier.send("telegram", {"signal": "恐慌极端"})

    assert notifier.attempt_count() == 1


def test_guard_never_persists_payload_or_secret(tmp_path):
    notifier = DisabledGoldNotifier(tmp_path / "gold_shadow")

    with pytest.raises(GoldExternalSendDenied):
        notifier.send("webhook", {"token": "must-not-persist"})

    text = (tmp_path / "gold_shadow" / "external_send_attempts.jsonl").read_text()
    assert "must-not-persist" not in text
    assert '"channel":"webhook"' in text


def test_attempt_count_is_durable_across_notifier_instances(tmp_path):
    root = tmp_path / "gold_shadow"
    notifier = DisabledGoldNotifier(root)

    with pytest.raises(GoldExternalSendDenied):
        notifier.send("feishu", {"secret": "never-persist"})

    assert attempt_count(root) == 1


@pytest.mark.parametrize("channel", ["", "Telegram", "telegram/extra", "telegram" * 20])
def test_channel_must_match_sanitized_lowercase_pattern(tmp_path, channel):
    notifier = DisabledGoldNotifier(tmp_path / "gold_shadow")

    with pytest.raises(ValueError, match="channel"):
        notifier.send(channel, {"signal": "恐慌极端"})

    assert not (tmp_path / "gold_shadow" / "external_send_attempts.jsonl").exists()


def test_malformed_attempt_state_fails_closed(tmp_path):
    root = tmp_path / "gold_shadow"
    root.mkdir()
    (root / "external_send_attempts.jsonl").write_text('{"schema_version":1}\n')

    with pytest.raises(ValueError, match="line 1"):
        attempt_count(root)


def test_symlinked_attempt_file_is_rejected(tmp_path):
    root = tmp_path / "gold_shadow"
    root.mkdir()
    target = tmp_path / "outside.jsonl"
    target.write_text("")
    (root / "external_send_attempts.jsonl").symlink_to(target)

    with pytest.raises(OSError):
        DisabledGoldNotifier(root).send("telegram", {})

    assert target.read_text() == ""


def test_attempt_storage_is_private_and_fsynced(tmp_path, monkeypatch):
    root = tmp_path / "gold_shadow"
    fsynced = []
    original_fsync = os.fsync

    def record_fsync(descriptor):
        fsynced.append(descriptor)
        original_fsync(descriptor)

    monkeypatch.setattr(os, "fsync", record_fsync)

    with pytest.raises(GoldExternalSendDenied):
        DisabledGoldNotifier(root).send("telegram", {})

    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert stat.S_IMODE((root / "external_send_attempts.jsonl").stat().st_mode) == 0o600
    assert len(fsynced) >= 2


def test_attempt_rows_are_only_schema_channel_and_timestamp(tmp_path):
    root = tmp_path / "gold_shadow"

    with pytest.raises(GoldExternalSendDenied):
        DisabledGoldNotifier(root).send("telegram", {"nested": {"token": "secret"}})

    row = json.loads((root / "external_send_attempts.jsonl").read_text())
    assert set(row) == {"schema_version", "attempted_at", "channel"}
    assert row["schema_version"] == 1
    assert row["channel"] == "telegram"
