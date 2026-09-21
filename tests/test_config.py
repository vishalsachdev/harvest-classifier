"""Configuration. A fresh install must classify nothing and send nothing."""
import pytest

from harvest_classifier.config import Config, ConfigError, load_config


def write(tmp_path, body, name="config.toml"):
    p = tmp_path / name
    p.write_text(body)
    return p


# --- defaults -----------------------------------------------------------------

def test_default_allow_list_is_empty():
    """Opt in explicitly. A fresh install must never send anything."""
    assert Config().allow_list == frozenset()


def test_a_default_install_classifies_nothing(tmp_path):
    config = load_config(None)
    assert config.allow_list == frozenset()
    assert config.is_classifiable("any-agent") is False


def test_defaults_ship_no_private_path_prefixes():
    assert Config().private_path_prefixes == ()


def test_default_freshness_and_status_dir_are_present_but_overridable():
    config = Config()
    assert config.fresh_seconds == 900
    assert config.status_dir.name == "fleet"


# --- loading ------------------------------------------------------------------

def test_allow_list_is_read_from_config(tmp_path):
    config = load_config(write(tmp_path, '[agents]\nallow = ["alpha", "beta"]\n'))
    assert config.allow_list == frozenset({"alpha", "beta"})
    assert config.is_classifiable("alpha") is True


def test_deny_outranks_allow(tmp_path):
    config = load_config(write(
        tmp_path, '[agents]\nallow = ["alpha", "beta"]\ndeny = ["beta"]\n'))
    assert config.is_classifiable("alpha") is True
    assert config.is_classifiable("beta") is False


def test_an_agent_in_neither_list_is_refused(tmp_path):
    config = load_config(write(tmp_path, '[agents]\nallow = ["alpha"]\n'))
    assert config.is_classifiable("gamma") is False


def test_names_needing_normalisation_are_refused(tmp_path):
    config = load_config(write(tmp_path, '[agents]\nallow = ["alpha"]\n'))
    for name in (" alpha ", "alpha\n", "alpha-2", "", None, 5):
        assert config.is_classifiable(name) is False


def test_matching_is_case_insensitive_on_both_lists(tmp_path):
    """Deliberate, and it is the deny side that forces it: a differently cased
    allow entry must not escape a deny entry (review item 10). The cost is that
    `Alpha` matches an allow entry of `alpha`, which is the safer direction to
    be wrong in, since agent names come from the operator's own producer."""
    config = load_config(write(tmp_path, '[agents]\nallow = ["alpha"]\n'))
    assert config.is_classifiable("Alpha") is True

    denied = load_config(write(
        tmp_path, '[agents]\nallow = ["Beta"]\ndeny = ["beta"]\n', "d.toml"))
    assert denied.is_classifiable("Beta") is False
    assert denied.is_classifiable("beta") is False


def test_status_dir_and_freshness_are_configurable(tmp_path):
    config = load_config(write(
        tmp_path, '[status]\ndir = "/tmp/statuses"\nfresh_seconds = 60\n'))
    assert str(config.status_dir) == "/tmp/statuses"
    assert config.fresh_seconds == 60


def test_home_is_expanded_in_paths(tmp_path):
    config = load_config(write(tmp_path, '[status]\ndir = "~/somewhere"\n'))
    assert "~" not in str(config.status_dir)


def test_private_path_prefixes_are_configurable(tmp_path):
    config = load_config(write(
        tmp_path, '[guard]\nprivate_path_prefixes = ["work/private", "notes"]\n'))
    assert config.private_path_prefixes == ("work/private", "notes")


def test_institution_identifier_pattern_is_configurable(tmp_path):
    config = load_config(write(
        tmp_path, '[guard]\ninstitution_identifier = "^[a-z]{3}\\\\d{5}$"\n'))
    assert config.institution_identifier == r"^[a-z]{3}\d{5}$"


def test_a_bad_regex_is_rejected_at_load_time(tmp_path):
    with pytest.raises(ConfigError, match="institution_identifier"):
        load_config(write(tmp_path, '[guard]\ninstitution_identifier = "([unclosed"\n'))


def test_unknown_top_level_section_is_fatal(tmp_path):
    with pytest.raises(ConfigError, match="surprise"):
        load_config(write(tmp_path, '[surprise]\nx = 1\n'))


def test_unknown_key_within_a_section_is_fatal(tmp_path):
    with pytest.raises(ConfigError, match="mystery"):
        load_config(write(tmp_path, '[agents]\nmystery = 1\n'))


def test_wrong_type_is_fatal(tmp_path):
    with pytest.raises(ConfigError, match="allow"):
        load_config(write(tmp_path, '[agents]\nallow = "alpha"\n'))


def test_missing_config_file_is_fatal_when_named(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.toml")


def test_malformed_toml_is_fatal(tmp_path):
    with pytest.raises(ConfigError, match="parse"):
        load_config(write(tmp_path, "[agents\n"))


# --- credentials --------------------------------------------------------------

def test_api_key_comes_from_the_environment_by_default(monkeypatch, tmp_path):
    monkeypatch.setenv("TYPESAFE_API_KEY", "from-env")
    assert load_config(None).read_api_key() == "from-env"


def test_api_key_file_is_used_when_configured(tmp_path, monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    key_file = tmp_path / "key"
    key_file.write_text("from-file\n")
    config = load_config(write(tmp_path, f'[provider]\napi_key_file = "{key_file}"\n'))
    assert config.read_api_key() == "from-file"


def test_a_missing_key_is_a_clear_error_not_a_silent_empty(tmp_path, monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    with pytest.raises(ConfigError, match="no API key"):
        load_config(None).read_api_key()


def test_the_key_never_appears_in_the_config_repr(tmp_path, monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "super-secret-value")
    config = load_config(None)
    assert "super-secret-value" not in repr(config)


def test_example_config_in_the_repo_loads():
    import pathlib
    example = pathlib.Path(__file__).resolve().parents[1] / "examples" / "config.example.toml"
    config = load_config(example)
    assert config.allow_list  # the example opts two invented agents in
