"""Who a session is, independent of how it was started.

A producer names its file from an environment variable when one is set, and
falls back to a pane or session id otherwise. Those ids match no allow-list
entry, so the sessions an operator has deliberately named get refused. Identity
is therefore resolved from the working directory the record already carries.
"""
import pytest

from harvest_classifier.config import Config, load_config
from harvest_classifier.identity import is_opaque_name, resolve_agent, under_denied_path


def rec(agent, cwd="/home/someone/projects/build-agent"):
    return {"agent": agent, "cwd": cwd}


@pytest.mark.parametrize("name", [
    "w5P:p1", "%1", "2d6a6fe1-56ab-49ec-8c87-7a635bc3e73a", "", "   ", None])
def test_opaque_names_are_recognised(name):
    assert is_opaque_name(name) is True


def test_a_plausible_name_is_left_alone_even_if_it_looks_id_like():
    """`pane_3` could be somebody's chosen agent name. A generic library
    cannot know it is a pane id, so it is not treated as one."""
    assert is_opaque_name("pane_3") is False


@pytest.mark.parametrize("name", ["build-agent", "docs-agent", "api-agent"])
def test_real_names_are_not_opaque(name):
    assert is_opaque_name(name) is False


def test_an_opaque_name_resolves_to_the_working_directory():
    assert resolve_agent(rec("w5P:p1")) == "build-agent"


def test_a_real_name_is_never_overridden():
    """An explicitly set name is deliberate; a path is a guess."""
    assert resolve_agent(rec("orchestrator")) == "orchestrator"


def test_resolution_lets_an_allow_list_entry_match_a_hand_started_session():
    config = Config(allow_list=frozenset({"build-agent"}))
    assert config.is_classifiable_record(rec("w5P:p1")) is True


def test_a_denied_path_beats_an_allow_listed_name(tmp_path):
    config = load_config_with(tmp_path, '''[agents]
allow = ["build-agent"]
deny_paths = ["private"]
''')
    assert config.is_classifiable_record(
        {"agent": "build-agent", "cwd": "/home/someone/private/build-agent"}) is False


def test_an_ordinary_path_is_not_denied(tmp_path):
    config = load_config_with(tmp_path, '''[agents]
allow = ["build-agent"]
deny_paths = ["private"]
''')
    assert config.is_classifiable_record(rec("build-agent")) is True


def test_a_missing_cwd_is_refused_once_deny_paths_are_configured(tmp_path):
    """Nothing can be verified about it, so it fails closed."""
    config = load_config_with(tmp_path, '''[agents]
allow = ["build-agent"]
deny_paths = ["private"]
''')
    assert config.is_classifiable_record({"agent": "build-agent", "cwd": None}) is False


def test_a_missing_cwd_is_tolerated_when_no_deny_paths_are_set():
    """Default install: there is no path rule, so there is nothing to verify."""
    config = Config(allow_list=frozenset({"build-agent"}))
    assert config.is_classifiable_record({"agent": "build-agent", "cwd": None}) is True


def test_deny_paths_match_whole_components_only(tmp_path):
    config = load_config_with(tmp_path, '''[agents]
allow = ["build-agent"]
deny_paths = ["private"]
''')
    assert config.is_classifiable_record(
        {"agent": "build-agent", "cwd": "/home/someone/private-notes/build-agent"}) is True


def load_config_with(tmp_path, body):
    path = tmp_path / "c.toml"
    path.write_text(body)
    return load_config(path)
