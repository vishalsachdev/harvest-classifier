"""The trust rule: freshness, ended sessions, and what counts as a message."""
import json
import time
from datetime import datetime, timedelta, timezone

import pytest

from harvest_classifier import status as S


def write(tmp_path, name, **over):
    rec = {"schema_version": 1, "agent": name, "pane_id": "w1:p1",
           "session_id": "sess-1", "cwd": "/x", "event": "Stop", "state": "idle",
           "updated_at": datetime.now(timezone.utc).astimezone().isoformat(),
           "last_assistant_text": "done", "blocked_reason": None}
    rec.update(over)
    p = tmp_path / f"{name}.json"
    p.write_text(json.dumps(rec))
    return p


def test_fresh_idle_record_is_trusted(tmp_path):
    rec = S.read_status(write(tmp_path, "build-agent"))
    assert S.is_trustworthy(rec) is True


def test_ended_session_is_not_trusted(tmp_path):
    rec = S.read_status(write(tmp_path, "build-agent", state="ended"))
    assert S.is_trustworthy(rec) is False


def test_stale_record_is_not_trusted(tmp_path):
    old = (datetime.now(timezone.utc) - timedelta(hours=2)).astimezone().isoformat()
    rec = S.read_status(write(tmp_path, "build-agent", updated_at=old))
    assert S.is_trustworthy(rec) is False


def test_unreadable_updated_at_is_not_trusted(tmp_path):
    rec = S.read_status(write(tmp_path, "build-agent", updated_at="not a date"))
    assert S.is_trustworthy(rec) is False


def test_missing_updated_at_is_not_trusted(tmp_path):
    rec = S.read_status(write(tmp_path, "build-agent", updated_at=None))
    assert S.is_trustworthy(rec) is False


def test_freshness_is_measured_from_updated_at_not_mtime(tmp_path):
    """mtime survives a copy or restore that never touched the session."""
    old = (datetime.now(timezone.utc) - timedelta(hours=2)).astimezone().isoformat()
    p = write(tmp_path, "build-agent", updated_at=old)
    import os
    os.utime(p, (time.time(), time.time()))   # fresh mtime, stale content
    assert S.is_trustworthy(S.read_status(p)) is False


def test_corrupt_json_returns_none_rather_than_raising(tmp_path):
    p = tmp_path / "build-agent.json"
    p.write_text("{not json")
    assert S.read_status(p) is None


def test_fingerprint_is_stable_and_excludes_message_text(tmp_path):
    rec = S.read_status(write(tmp_path, "build-agent"))
    a = S.fingerprint(rec)
    assert a == S.fingerprint(rec)
    assert "done" not in a


def test_fingerprint_changes_when_the_message_changes(tmp_path):
    a = S.fingerprint(S.read_status(write(tmp_path, "build-agent")))
    b = S.fingerprint(S.read_status(write(tmp_path, "build-agent",
                                          last_assistant_text="something else")))
    assert a != b


def test_dedupe_key_is_session_id_plus_updated_at(tmp_path):
    rec = S.read_status(write(tmp_path, "build-agent"))
    assert S.dedupe_key(rec) == ("sess-1", rec["updated_at"])


def test_iter_status_files_skips_non_json(tmp_path):
    write(tmp_path, "build-agent")
    (tmp_path / "notes.txt").write_text("x")
    assert [p.name for p in S.iter_status_files(tmp_path)] == ["build-agent.json"]


def test_working_record_with_no_final_message_is_not_classifiable(tmp_path):
    rec = S.read_status(write(tmp_path, "build-agent", state="working",
                              last_assistant_text=""))
    assert S.has_message(rec) is False


def test_explicit_null_message_is_handled(tmp_path):
    """A `working` record carries last_assistant_text: null, not \"\"."""
    rec = S.read_status(write(tmp_path, "build-agent", state="working",
                              last_assistant_text=None))
    assert S.has_message(rec) is False


def test_fingerprint_of_a_null_message_does_not_raise(tmp_path):
    rec = S.read_status(write(tmp_path, "build-agent", last_assistant_text=None))
    assert isinstance(S.fingerprint(rec), str)
