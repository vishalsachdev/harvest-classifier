"""Provider behaviour. No network, no key: a counting fake transport throughout.

Covers the review's items 5, 6, 7 and 13 and four of its plausible items.
"""
import json
import pathlib

import pytest

from harvest_classifier import provider
from harvest_classifier.config import Config, ConfigError
from harvest_classifier.provider import ProviderError, ask

QUESTIONS = {"claims_done": {"type": "noul", "instructions": "?"},
             "next_action": {"type": "choice", "instructions": "?",
                             "criteria": {"harvest": "a", "ignore": "b"}}}

GOOD = {"model": "jev-1.13.0",
        "answers": {"claims_done": {"type": "noul", "noul": 0.8},
                    "next_action": {"type": "choice", "choice": "harvest",
                                    "probabilities": {"harvest": 0.9, "ignore": 0.1},
                                    "confidence": 0.77}},
        "usage": {"input_tokens": 120, "output_tokens": 0}}


class Counting:
    """Fake transport that records every call and can fail on cue."""

    def __init__(self, *outcomes):
        self.calls = 0
        self.bodies = []
        self._outcomes = list(outcomes)

    def post(self, body):
        self.calls += 1
        self.bodies.append(body)
        outcome = self._outcomes.pop(0) if self._outcomes else GOOD
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


# --- retry budget -------------------------------------------------------------

def test_a_retryable_failure_is_retried_once_then_succeeds():
    t = Counting(ProviderError("529", status=529, retryable=True), GOOD)
    out = ask(t, "text", QUESTIONS, "jev-1.13.0")
    assert t.calls == 2 and out["_attempts"] == 2


def test_never_more_than_two_attempts():
    t = Counting(*[ProviderError("529", status=529, retryable=True)] * 5)
    with pytest.raises(ProviderError):
        ask(t, "text", QUESTIONS, "jev-1.13.0")
    assert t.calls == 2


@pytest.mark.parametrize("status_code", [400, 401, 403, 404, 422])
def test_never_retries_a_client_error(status_code):
    t = Counting(ProviderError(str(status_code), status=status_code, retryable=False))
    with pytest.raises(ProviderError):
        ask(t, "text", QUESTIONS, "jev-1.13.0")
    assert t.calls == 1, f"{status_code} must not be retried"


def test_a_retry_reuses_the_identical_body():
    t = Counting(ProviderError("529", status=529, retryable=True), GOOD)
    ask(t, "text", QUESTIONS, "jev-1.13.0")
    assert t.bodies[0] == t.bodies[1]


# --- no fallback --------------------------------------------------------------

def test_the_pinned_model_is_the_only_model_ever_requested():
    t = Counting(ProviderError("529", status=529, retryable=True), GOOD)
    ask(t, "text", QUESTIONS, "jev-1.13.0")
    assert {b["model"] for b in t.bodies} == {"jev-1.13.0"}


def test_a_different_returned_model_is_refused():
    """A pinned id is pointless if a different model may answer to it."""
    t = Counting({**GOOD, "model": "jev-1.14.0"})
    with pytest.raises(ProviderError, match="model"):
        ask(t, "text", QUESTIONS, "jev-1.13.0")


def test_no_model_id_and_only_one_endpoint_are_hard_coded():
    """The model comes from configuration; the endpoint is the only URL the
    module can post to."""
    import re
    source = pathlib.Path(provider.__file__).read_text()
    assert re.findall(r"jev-[0-9.]+", source) == []
    posts = re.findall(r'^ENDPOINT = "(.+)"$', source, re.MULTILINE)
    assert posts == ["https://api.typesafe.ai/v1/systemone"]


# --- malformed responses ------------------------------------------------------

@pytest.mark.parametrize("body", [
    {}, {"answers": None}, {"answers": []}, {"answers": [1]},
    {"answers": "text"}, [1, 2, 3], "a string", None,
    {"answers": {"claims_done": "not a dict"}},
    {"answers": {}},
])
def test_a_malformed_response_is_a_non_retryable_provider_error(body):
    t = Counting(body)
    with pytest.raises(ProviderError) as exc:
        ask(t, "text", QUESTIONS, "jev-1.13.0")
    assert exc.value.retryable is False
    assert t.calls == 1


def test_an_empty_answer_set_never_becomes_a_silent_ignore():
    """It used to return a row saying `ignore, decided_by=model`, which dedupe
    then never retried."""
    with pytest.raises(ProviderError):
        ask(Counting({"model": "jev-1.13.0", "answers": {}}), "t", QUESTIONS, "jev-1.13.0")


def test_a_null_probability_does_not_reach_policy_as_a_comparison():
    t = Counting({**GOOD, "answers": {**GOOD["answers"],
                                      "claims_done": {"type": "noul", "noul": None}}})
    with pytest.raises(ProviderError):
        ask(t, "text", QUESTIONS, "jev-1.13.0")


def test_null_input_tokens_do_not_crash_the_cost_calculation():
    t = Counting({**GOOD, "usage": {"input_tokens": None}})
    out = ask(t, "text", QUESTIONS, "jev-1.13.0")
    assert out["_input_tokens"] == 0 and out["_usd"] == 0.0


def test_a_missing_usage_block_is_tolerated():
    body = {k: v for k, v in GOOD.items() if k != "usage"}
    out = ask(Counting(body), "text", QUESTIONS, "jev-1.13.0")
    assert out["_input_tokens"] == 0


# --- the live index-keyed shape ----------------------------------------------

#: Sanitized recording of the shape the live API returns for a Choice whose
#: options come back index-keyed, with the legend mapping index to option.
#: This shape was met for real on Scores in a sibling project; it is recorded
#: here rather than called for, because no live call is permitted.
INDEX_KEYED = {
    "model": "jev-1.13.0",
    "answers": {
        "claims_done": {"type": "noul", "noul": 0.42},
        "next_action": {"type": "choice", "choice": "1",
                        "legend": {"0": "harvest", "1": "ignore"},
                        "probabilities": {"0": 0.2, "1": 0.8},
                        "confidence": 0.61},
    },
    "usage": {"input_tokens": 90, "output_tokens": 0},
}


def test_an_index_keyed_choice_is_mapped_back_to_its_option_name():
    """Unmapped, next_action becomes '1' and every model row logs `ignore`."""
    out = ask(Counting(INDEX_KEYED), "text", QUESTIONS, "jev-1.13.0")
    assert out["next_action"] == "ignore"
    assert out["next_action_confidence"] == 0.61


def test_an_index_keyed_choice_with_no_legend_is_refused():
    body = json.loads(json.dumps(INDEX_KEYED))
    del body["answers"]["next_action"]["legend"]
    with pytest.raises(ProviderError):
        ask(Counting(body), "text", QUESTIONS, "jev-1.13.0")


def test_an_index_outside_the_legend_is_refused():
    body = json.loads(json.dumps(INDEX_KEYED))
    body["answers"]["next_action"]["choice"] = "9"
    with pytest.raises(ProviderError):
        ask(Counting(body), "text", QUESTIONS, "jev-1.13.0")


# --- transport hardening ------------------------------------------------------

def test_the_opener_refuses_redirects():
    """urllib follows redirects and forwards Authorization, so a 302 would
    hand the key to whatever host the redirect names."""
    import urllib.error
    handler = provider.NoRedirect()
    with pytest.raises(urllib.error.HTTPError):
        handler.redirect_request(None, None, 302, "Found", {}, "https://elsewhere.example")


def test_a_response_body_is_capped():
    assert provider.MAX_RESPONSE_BYTES <= 1_000_000


def test_transport_errors_are_all_provider_errors(monkeypatch):
    """Timeouts, TLS failures and truncated reads must not escape as
    themselves: they kill --watch."""
    import http.client
    import socket
    import ssl
    import urllib.error

    for raised in (TimeoutError("slow"), socket.timeout("slow"),
                   ssl.SSLError("tls"), http.client.RemoteDisconnected("cut"),
                   urllib.error.URLError("down"), json.JSONDecodeError("bad", "x", 0)):
        def boom(*a, **k):
            raise raised
        monkeypatch.setattr(provider.urllib.request, "urlopen", boom)
        transport = provider.LiveTransport(lambda: "key")
        with pytest.raises(ProviderError):
            transport.post({"state": "x", "model": "m", "questions": {}})


# --- key handling (item 5) ----------------------------------------------------

def test_a_multiline_key_file_is_refused(tmp_path):
    key_file = tmp_path / "key"
    key_file.write_text("line-one\nline-two\n")
    config = Config(api_key_file=key_file)
    with pytest.raises(ConfigError) as exc:
        config.read_api_key()
    assert "line-one" not in str(exc.value) and "line-two" not in str(exc.value)


@pytest.mark.parametrize("raw", ["abc def", "abc\tdef", "abc\x01def", "abc def"])
def test_a_key_with_forbidden_characters_is_refused(tmp_path, raw):
    key_file = tmp_path / "key"
    key_file.write_text(raw)
    with pytest.raises(ConfigError) as exc:
        Config(api_key_file=key_file).read_api_key()
    assert raw.strip() not in str(exc.value)


def test_a_normal_key_still_loads(tmp_path):
    # Built at runtime rather than written as a literal: a key-shaped string in
    # a public repository trips other people's secret scanners, including
    # GitHub push protection.
    fake_key = "sk-" + "ABCdef0123456789"
    key_file = tmp_path / "key"
    key_file.write_text(fake_key + "\n")
    assert Config(api_key_file=key_file).read_api_key() == fake_key
