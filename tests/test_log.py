import json
import os

import pytest

from harvest_classifier import log as L


def test_row_is_written_and_read_back(tmp_path):
    p = tmp_path / "log.jsonl"
    L.append({"agent": "build-agent", "recommendation": "harvest",
              "session_id": "s1", "updated_at": "t1"}, p)
    rows = L.read(p)
    assert rows[0]["agent"] == "build-agent"
    assert rows[0]["schema"] == 1


def test_message_text_can_never_be_written(tmp_path):
    p = tmp_path / "log.jsonl"
    L.append({"agent": "a", "last_assistant_text": "SECRET BODY",
              "message": "also secret", "recommendation": "ignore"}, p)
    assert "SECRET BODY" not in p.read_text()
    assert "also secret" not in p.read_text()


def test_only_declared_fields_survive(tmp_path):
    p = tmp_path / "log.jsonl"
    row = L.append({"agent": "a", "surprise": 1}, p)
    assert "surprise" not in row


def test_file_and_directory_modes_are_owner_only(tmp_path):
    d = tmp_path / "shadow"
    p = d / "log.jsonl"
    L.append({"agent": "a"}, p)
    assert oct(os.stat(d).st_mode)[-3:] == "700"
    assert oct(os.stat(p).st_mode)[-3:] == "600"


def test_seen_keys_dedupe_on_session_and_updated_at(tmp_path):
    p = tmp_path / "log.jsonl"
    L.append({"agent": "a", "session_id": "s1", "updated_at": "t1"}, p)
    assert ("s1", "t1") in L.seen_keys(p)
    assert ("s1", "t2") not in L.seen_keys(p)


def test_corrupt_line_is_skipped_not_fatal(tmp_path):
    p = tmp_path / "log.jsonl"
    L.append({"agent": "a"}, p)
    with open(p, "a") as h:
        h.write("{not json\n")
    assert len(L.read(p)) == 1


def test_missing_log_reads_as_empty(tmp_path):
    assert L.read(tmp_path / "nope.jsonl") == []
