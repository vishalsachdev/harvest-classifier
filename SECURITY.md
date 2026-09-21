<!-- audience: external -->
# Security

## What leaves the machine, and when

Exactly one thing can leave: the text of a final message, sent to the TypeSafe
API, and only when **all** of these are true.

1. The session's name is in `[agents].allow` and not in `[agents].deny`.
   The allow-list ships empty, so a fresh install sends nothing.
2. The status record is fresh and its session has not ended.
3. The message is not empty.
4. The pre-send guard found nothing: no email address, no token shaped string,
   no institutional identifier, no name next to a grade or score, no configured
   private path, and the message is under 20,000 characters.
5. The deterministic checks did not already settle the recommendation.
6. You did not pass `--dry-run`, which makes no network call at all.

Nothing else is transmitted. Not file contents, not the status file, not your
configuration, not the log.

## What is written to disk

Rows in `<log.dir>/harvest-log.jsonl`, directory `700` and file `600`. A row
holds the session name, identifiers, timestamps, a **SHA-256 fingerprint** of
the message and its length, the deterministic flags, the model's answers, the
recommendation, cost and latency.

**The message text itself is never written.** Rows go through a field
allow-list, and a test writes message text into a row and asserts it does not
reach the file.

Note what the fingerprint is: a hash of the message, useful for matching a
label to a row. It is not an anonymisation of the message, and a short message
could in principle be recovered by guessing. Treat the log as private.

## Credentials

The API key is read from the `TYPESAFE_API_KEY` environment variable, or from a
file named by `provider.api_key_file`. It is read at call time, is never stored
on an object, and never appears in a row, a log line, an error message or the
configuration's `repr`.

The value is validated before use: it must be printable ASCII with no
whitespace. A key carrying a newline or a space makes the HTTP layer raise an
error whose text contains the key, which would then reach a traceback. The
error raised instead names only where the key came from.

Redirects are refused outright. `urllib` follows them and forwards the
`Authorization` header while doing it, so a 302 from the endpoint would hand
the key to whichever host the redirect names.

## Guard limitations

The guard is pattern matching and it will miss things. It is the second line of
defence. **The allow-list is the control that matters**: do not allow-list a
session whose final messages can contain other people's data, including any
orchestrator session that quotes other sessions back.

The institutional identifier pattern is configurable because the useful pattern
differs by organisation. The default is deliberately generic and will both miss
real identifiers and occasionally fire on ordinary words.

## Reporting a problem

Please open a GitHub issue for ordinary bugs. For anything you believe is a
disclosure risk, do not open a public issue: use GitHub's private vulnerability
reporting on this repository instead, and allow a few days for a first reply.

This is a personal project maintained in spare time, with no service level
commitment.
