<!-- audience: external -->
# The status file contract

The classifier does not watch your agents. It reads small JSON files that a
producer writes, one per session. Anything that writes files in this shape will
work; `examples/status_hook.py` is one reference producer.

## Location and permissions

One file per session, named for the session, in the configured `status.dir`
(default `~/.claude/state/fleet`). Directory `700`, files `600`, written to a
temporary file and renamed so a reader never sees a half-written file.

These files contain the final messages of your sessions. Treat them as private.

## Fields

| Field | Type | Meaning |
|---|---|---|
| `schema_version` | integer | `1`. |
| `agent` | string | The session's name. This is what the allow-list matches, so it must be stable across turns. |
| `session_id` | string | Identifies the session. Rows are deduplicated on this plus `updated_at`. |
| `state` | string | One of `working`, `idle`, `blocked`, `ended`. |
| `event` | string | The event that produced this write, for debugging. |
| `updated_at` | string | ISO 8601 with offset. **Freshness is measured from this**, not from the file's mtime, because an mtime survives a copy or a restore that never touched the session. |
| `last_assistant_text` | string or null | The final message of the turn. Explicitly `null` while a turn is in progress. |
| `blocked_reason` | string or null | Why the session is blocked, when `state` is `blocked`. |
| `turn_started_at` | string or null | Optional. When the current turn began. |
| `cwd` | string or null | Optional. |

Unknown extra fields are ignored by the reader.

## What the classifier does with it

A record is used only when all of these hold:

* the `agent` name is allow-listed and not deny-listed. If the name is opaque,
  a pane id or a session id rather than something a person chose, it is
  resolved to the basename of `cwd` first, so a session started by hand is
  still recognised;
* `cwd` is not under a configured `deny_paths` entry;
* `state` is not `ended`;
* `updated_at` parses and is within `status.fresh_seconds`;
* `last_assistant_text` is a non-empty string.

Otherwise the record is skipped, silently and without a row.

## A check this package cannot make

If a producer writes one file per terminal pane and a pane is recycled under a
new session, the file may describe a session that no longer occupies it.
Confirming that needs the terminal multiplexer, which this package deliberately
never talks to. A recycled pane writes a new `session_id`, rows are keyed on it,
and freshness bounds the staleness, so for a classifier that never acts this
costs data quality rather than safety. `status.is_trustworthy` takes an
`occupancy_check` argument if your caller can answer the question.

## Two facts worth knowing if you write your own producer

* The blocking notification payload field is **`notification_type`**. Some
  published documentation calls it `type`, and the real payload has no `type`
  key at all. A producer coded from that documentation will never mark a
  session blocked, and no unit test written from the same documentation will
  catch it.
* **A permission request that the user denies fires no event.** A session can
  therefore sit blocked with the file still saying `working`. Freshness is what
  bounds this: a stale file stops being trusted.
