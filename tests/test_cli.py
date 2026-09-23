"""CLI behaviour. Every command is offline here: no key, no network."""
import json
from datetime import datetime, timezone

from harvest_classifier.cli import main


def write_status(d, agent, text, session="s1", STATE="idle", BLOCKED=None):
    (d / f"{agent}.json").write_text(json.dumps({
        "schema_version": 1, "agent": agent, "session_id": session,
        "state": STATE, "event": "Stop", "blocked_reason": BLOCKED,
        "updated_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "last_assistant_text": text}))


def setup(tmp_path, allow=("build-agent",), deny=("orchestrator",)):
    status = tmp_path / "status"
    status.mkdir(exist_ok=True)
    config = tmp_path / "config.toml"
    config.write_text(
        f'[agents]\nallow = {list(allow)!r}\ndeny = {list(deny)!r}\n'
        f'[status]\ndir = "{status}"\n[log]\ndir = "{tmp_path / "log"}"\n'.replace("'", '"'))
    return status, config, tmp_path / "log" / "harvest-log.jsonl"


def harvest(config, *extra):
    return main(["--config", str(config), "shadow-harvest", "--once", "--dry-run", *extra])


def test_a_default_install_refuses_to_run_with_an_empty_allow_list(tmp_path, capsys):
    config = tmp_path / "empty.toml"
    config.write_text("[agents]\nallow = []\n")
    assert harvest(config) == 1
    assert "no agents are allow-listed" in capsys.readouterr().err


def test_once_writes_rows_only_for_allow_listed_agents(tmp_path):
    status, config, log_path = setup(tmp_path)
    write_status(status, "build-agent", "the approval prompt is waiting",
                 STATE="blocked", BLOCKED="permission_prompt")
    write_status(status, "orchestrator", "everything is fine")
    write_status(status, "stranger", "also fine")
    assert harvest(config) == 0
    rows = [json.loads(l) for l in log_path.read_text().splitlines()]
    assert [r["agent"] for r in rows] == ["build-agent"]


def test_a_denied_agent_is_refused_even_when_also_allowed(tmp_path):
    status, config, log_path = setup(tmp_path, allow=("build-agent", "orchestrator"))
    write_status(status, "orchestrator", "a permission dialog is open")
    assert harvest(config) == 0
    assert not log_path.exists() or log_path.read_text().strip() == ""


def test_once_is_idempotent_on_the_same_status(tmp_path):
    status, config, log_path = setup(tmp_path)
    write_status(status, "build-agent", "a permission dialog is open",
                 STATE="blocked", BLOCKED="permission_prompt")
    harvest(config)
    harvest(config)
    assert len(log_path.read_text().strip().splitlines()) == 1


def test_a_new_turn_is_a_new_row(tmp_path):
    import time
    status, config, log_path = setup(tmp_path)
    write_status(status, "build-agent", "a permission dialog is open",
                 STATE="blocked", BLOCKED="permission_prompt")
    harvest(config)
    time.sleep(0.01)
    write_status(status, "build-agent", "now waiting on approval instead",
                 STATE="blocked", BLOCKED="permission_prompt")
    harvest(config)
    assert len(log_path.read_text().strip().splitlines()) == 2


def test_dry_run_makes_no_network_call(tmp_path, monkeypatch):
    import socket
    monkeypatch.setattr(socket.socket, "connect",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("network")))
    status, config, _ = setup(tmp_path)
    write_status(status, "build-agent", "Reading the spec now.")
    assert harvest(config) == 0


def test_no_api_key_is_needed_for_a_dry_run(tmp_path, monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    status, config, _ = setup(tmp_path)
    write_status(status, "build-agent", "Reading the spec now.")
    assert harvest(config) == 0


def test_label_then_report_computes_agreement(tmp_path, capsys):
    status, config, _ = setup(tmp_path)
    write_status(status, "build-agent", "a permission dialog is open",
                 STATE="blocked", BLOCKED="permission_prompt")
    harvest(config)
    assert main(["--config", str(config), "shadow-label", "build-agent", "unblock"]) == 0
    main(["--config", str(config), "shadow-report"])
    assert "agreement: 1/1" in capsys.readouterr().out


def test_label_for_an_unclassified_agent_fails_cleanly(tmp_path):
    _, config, _ = setup(tmp_path)
    assert main(["--config", str(config), "shadow-label", "docs-agent", "harvest"]) == 1


def test_report_on_an_empty_log_does_not_crash(tmp_path, capsys):
    _, config, _ = setup(tmp_path)
    assert main(["--config", str(config), "shadow-report"]) == 0
    assert "no rows yet" in capsys.readouterr().out


def test_a_bad_config_is_a_clear_error_not_a_traceback(tmp_path, capsys):
    bad = tmp_path / "bad.toml"
    bad.write_text("[agents]\nmystery = 1\n")
    assert main(["--config", str(bad), "shadow-report"]) == 2
    assert "configuration error" in capsys.readouterr().err


def test_questions_command_prints_exactly_what_would_be_sent(tmp_path, capsys):
    assert main(["questions"]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["next_action"]["type"] == "choice"
