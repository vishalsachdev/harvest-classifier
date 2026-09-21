"""The example producer is example code, but it writes the input this tool
trusts, so its defects are this tool's problem too.

Loaded by path rather than imported, because it lives under examples/ and is
not part of the package.
"""
import importlib.util
import json
import os
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
HOOK = ROOT / "examples" / "status_hook.py"


def load_hook(monkeypatch, status_dir):
    """Import the example as a module with its status dir pointed at tmp."""
    monkeypatch.setenv("HARVEST_STATUS_DIR", str(status_dir))
    for key in ("HARVEST_AGENT", "TERM_SESSION_ID"):
        monkeypatch.delenv(key, raising=False)
    sys.modules.pop("status_hook", None)
    spec = importlib.util.spec_from_file_location("status_hook", HOOK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- 16. the file name must stay inside the status directory -----------------

@pytest.mark.parametrize("session_id", [
    "../../escape", "a/b", "..", ".", "", "  ", "/absolute", "x" * 500,
])
def test_item16_a_hostile_session_id_cannot_escape_the_directory(monkeypatch, tmp_path, session_id):
    hook = load_hook(monkeypatch, tmp_path / "status")
    name = hook._agent_name({"session_id": session_id})
    assert "/" not in name and ".." not in name
    assert name
    resolved = (tmp_path / "status" / f"{name}.json").resolve()
    assert resolved.parent == (tmp_path / "status").resolve()


def test_item16_an_environment_agent_name_is_also_sanitised(monkeypatch, tmp_path):
    hook = load_hook(monkeypatch, tmp_path / "status")
    monkeypatch.setenv("HARVEST_AGENT", "../../../etc/passwd")
    name = hook._agent_name({})
    assert "/" not in name and ".." not in name


def test_item16_a_corrupt_previous_file_does_not_wedge_the_hook(monkeypatch, tmp_path):
    """A non-dict previous file used to raise on every call, so that session's
    file could never update again."""
    status_dir = tmp_path / "status"
    status_dir.mkdir()
    hook = load_hook(monkeypatch, status_dir)
    (status_dir / "sess.json").write_text("[1, 2, 3]")
    assert hook._previous(status_dir / "sess.json") == {}


# --- 15. a stale message must not be carried into a new turn -----------------

def test_item15_session_start_clears_the_previous_message(monkeypatch, tmp_path):
    hook = load_hook(monkeypatch, tmp_path / "status")
    previous = {"session_id": "s1", "last_assistant_text": "yesterday's message",
                "state": "idle"}
    record = hook._next_record("SessionStart", {"session_id": "s1"}, previous)
    assert record["last_assistant_text"] is None


def test_item15_a_new_session_id_clears_the_previous_message(monkeypatch, tmp_path):
    """Otherwise yesterday's message passes freshness under a new id and is
    classified again."""
    hook = load_hook(monkeypatch, tmp_path / "status")
    previous = {"session_id": "old", "last_assistant_text": "yesterday's message",
                "state": "idle"}
    record = hook._next_record("Notification", {"session_id": "new"}, previous)
    assert record["last_assistant_text"] is None


def test_item15_the_same_session_keeps_its_message_across_a_notification(monkeypatch, tmp_path):
    hook = load_hook(monkeypatch, tmp_path / "status")
    previous = {"session_id": "s1", "last_assistant_text": "today's message",
                "state": "idle"}
    record = hook._next_record("Notification", {"session_id": "s1",
                                                "notification_type": "idle_prompt"},
                               previous)
    assert record["last_assistant_text"] == "today's message"


# --- plausible: a top-level session carrying agent_type ----------------------

def test_a_subagent_payload_is_still_ignored(monkeypatch, tmp_path):
    hook = load_hook(monkeypatch, tmp_path / "status")
    assert hook._is_subagent({"agent_id": "sub-1", "session_id": "s"}) is True


def test_a_top_level_session_with_a_null_agent_type_is_not_a_subagent(monkeypatch, tmp_path):
    """`agent_type: null` was treated as a subagent marker, so such a session
    never wrote a file at all."""
    hook = load_hook(monkeypatch, tmp_path / "status")
    assert hook._is_subagent({"agent_type": None, "session_id": "s"}) is False
    assert hook._is_subagent({"agent_id": "", "session_id": "s"}) is False


# --- the notification field, and the write ------------------------------------

def test_the_notification_field_name_is_accepted_both_ways(monkeypatch, tmp_path):
    hook = load_hook(monkeypatch, tmp_path / "status")
    for payload in ({"notification_type": "permission_prompt"},
                    {"type": "permission_prompt"}):
        record = hook._next_record("Notification", {**payload, "session_id": "s"}, {})
        assert record["state"] == "blocked"
        assert record["blocked_reason"] == "permission_prompt"


def test_the_written_file_is_owner_only_and_valid(monkeypatch, tmp_path):
    import stat as stat_module
    status_dir = tmp_path / "status"
    hook = load_hook(monkeypatch, status_dir)
    record = hook._next_record("Stop", {"session_id": "s1",
                                        "last_assistant_message": "done"}, {})
    path = status_dir / "s1.json"
    hook._write_atomically(path, record)
    assert stat_module.S_IMODE(os.stat(path).st_mode) == 0o600
    assert json.loads(path.read_text())["last_assistant_text"] == "done"
