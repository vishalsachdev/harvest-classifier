"""TypeSafe Jev transport, standard library only.

Composed rather than configured: the caller supplies the question set and owns
the retry budget, so a second profile can reuse the transport without changing
how this one behaves.

Independent of, and not endorsed by, TypeSafe AI. See https://typesafe.ai and
https://docs.typesafe.ai for the service this talks to.
"""
from __future__ import annotations

import http.client
import json
import ssl
import urllib.error
import urllib.request
from typing import Any

ENDPOINT = "https://api.typesafe.ai/v1/systemone"

#: One retry, and only for conditions that can plausibly clear. An auth or
#: validation failure is this program's bug and repeating it just costs money.
MAX_ATTEMPTS = 2
RETRYABLE_STATUS = {429, 500, 502, 503, 529}

#: Documented input price, USD per million input tokens. Output is not billed.
USD_PER_M_INPUT_TOKENS = 0.042

#: A response larger than this is not a response to these questions. Without a
#: cap the body is read unbounded into memory.
MAX_RESPONSE_BYTES = 1_000_000


class ProviderError(RuntimeError):
    def __init__(self, message: str, status: int | None = None, retryable: bool = False):
        super().__init__(message)
        self.status = status
        self.retryable = retryable


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse every redirect.

    urllib follows redirects and forwards the Authorization header while doing
    it, so a 302 from the endpoint would hand the API key to whatever host the
    redirect names.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(
            newurl, code, "redirects are refused: the Authorization header "
            "must not follow a redirect", headers, fp)


def _ssl_context() -> ssl.SSLContext:
    """Verified TLS, with certifi if it happens to be installed.

    Some Python builds on macOS ship without a usable CA bundle. If you see a
    certificate error, either run that build's "Install Certificates" step or
    `pip install certifi`; this package never falls back to an unverified
    context.
    """
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


class FixtureTransport:
    """Offline transport. Returns a canned response; touches no network."""

    def __init__(self, payload: dict[str, Any]):
        self._payload = payload

    def post(self, body: dict[str, Any]) -> dict[str, Any]:
        return self._payload


class LiveTransport:
    """One POST, no built-in retry: the caller owns the attempt budget."""

    def __init__(self, api_key_reader):
        self._read_key = api_key_reader

    def post(self, body: dict[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(
            ENDPOINT,
            data=json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {self._read_key()}",
                     "Content-Type": "application/json"},
            method="POST",
        )
        opener = urllib.request.build_opener(
            NoRedirect(), urllib.request.HTTPSHandler(context=_ssl_context()))
        try:
            with opener.open(request, timeout=60) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise ProviderError("response larger than the cap", retryable=False)
            return json.loads(raw)
        except urllib.error.HTTPError as exc:
            raise ProviderError(f"HTTP {exc.code}", status=exc.code,
                                retryable=exc.code in RETRYABLE_STATUS) from None
        except urllib.error.URLError as exc:
            raise ProviderError(f"connection failed: {exc.reason}",
                                retryable=True) from None
        except (TimeoutError, ssl.SSLError, http.client.HTTPException,
                ConnectionError, OSError) as exc:
            # These used to escape as themselves and kill a --watch loop.
            raise ProviderError(f"transport failure: {type(exc).__name__}",
                                retryable=True) from None
        except json.JSONDecodeError:
            raise ProviderError("response was not JSON", retryable=False) from None


def _validate(response: Any, model: str) -> dict[str, Any]:
    """Every shape check, in one place, all non-retryable.

    A malformed response used to flow through as an empty answer set, which
    became a permanent `ignore` row that deduplication never retried.
    """
    if not isinstance(response, dict):
        raise ProviderError(
            f"response is {type(response).__name__}, expected an object",
            retryable=False)
    returned = response.get("model")
    if returned is not None and returned != model:
        raise ProviderError(
            f"response model {returned!r} is not the pinned model {model!r}",
            retryable=False)
    answers = response.get("answers")
    if not isinstance(answers, dict) or not answers:
        raise ProviderError("response carries no answers", retryable=False)
    for name, answer in answers.items():
        if not isinstance(answer, dict):
            raise ProviderError(f"answer {name!r} is not an object", retryable=False)
        kind = answer.get("type")
        if kind == "noul":
            value = answer.get("noul")
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise ProviderError(f"answer {name!r} has no numeric value",
                                    retryable=False)
        elif kind == "choice":
            if not isinstance(answer.get("choice"), str):
                raise ProviderError(f"answer {name!r} has no choice", retryable=False)
        else:
            raise ProviderError(f"answer {name!r} has unknown type {kind!r}",
                                retryable=False)
    return answers


def _resolve_choice(name: str, answer: dict[str, Any]) -> str:
    """Map an index-keyed choice back to its option name.

    The service may answer a Choice with the option's index as a string and a
    legend mapping index to option. Unmapped, `next_action` becomes "1" and
    every model row silently logs `ignore`.
    """
    chosen = answer["choice"]
    legend = answer.get("legend")
    if legend is None or chosen in set(legend.values() if isinstance(legend, dict) else legend):
        if isinstance(legend, dict) and chosen in legend:
            return legend[chosen]
        return chosen
    if isinstance(legend, dict):
        if chosen in legend:
            return legend[chosen]
        raise ProviderError(f"answer {name!r} chose {chosen!r}, absent from its legend",
                            retryable=False)
    if isinstance(legend, list):
        try:
            return legend[int(chosen)]
        except (ValueError, IndexError):
            raise ProviderError(
                f"answer {name!r} chose {chosen!r}, absent from its legend",
                retryable=False) from None
    raise ProviderError(f"answer {name!r} has an unusable legend", retryable=False)


def ask(transport, text: str, questions: dict[str, dict], model: str) -> dict[str, Any]:
    """One request, flattened. No fallback to any other model, ever.

    A failure is reported as a failure. Silently answering with a different
    model would make a result unattributable, which defeats pinning the id.
    """
    body = {"state": text, "model": model, "questions": questions}
    attempts = 0
    last: ProviderError | None = None
    response: dict[str, Any] | None = None
    while attempts < MAX_ATTEMPTS:
        attempts += 1
        try:
            response = transport.post(body)
            break
        except ProviderError as exc:
            last = exc
            if not exc.retryable:
                raise
    if response is None:
        raise last or ProviderError("no response")

    answers = _validate(response, model)
    flat: dict[str, Any] = {}
    for name, answer in answers.items():
        if answer["type"] == "noul":
            flat[name] = float(answer["noul"])
        else:
            chosen = _resolve_choice(name, answer)
            declared = (questions.get(name) or {}).get("criteria")
            if isinstance(declared, dict) and chosen not in declared:
                # The service answered with something outside the options it
                # was given, most often a bare index with no legend to map it.
                raise ProviderError(
                    f"answer {name!r} chose {chosen!r}, which is not one of the "
                    "declared options", retryable=False)
            flat[name] = chosen
            flat[f"{name}_confidence"] = answer.get("confidence")
    tokens = (response.get("usage") or {}).get("input_tokens") or 0
    if not isinstance(tokens, (int, float)) or isinstance(tokens, bool):
        tokens = 0
    flat["_model"] = response.get("model")
    flat["_input_tokens"] = tokens
    flat["_usd"] = tokens / 1e6 * USD_PER_M_INPUT_TOKENS
    flat["_attempts"] = attempts
    return flat
