<!-- audience: external -->
# harvest-classifier

An orchestrator running several coding agents spends a turn reading each one's
final message. Most of those messages need nothing: they report progress, or
they finish work that is plainly finished. A few need something specific, and
telling the two apart is the whole job.

This is a shadow classifier for that job. It reads the status files your agent
sessions write, decides what it *would* recommend, and writes a row to a local
log. It never acts. You read the rows and decide whether it was right before
anything is wired to its output.

Five recommendations: `harvest`, `bounce_for_evidence`, `unblock`,
`escalate_to_owner`, `ignore`.

## How it decides

Full walkthrough with a diagram: [docs/pipeline.html](docs/pipeline.html)
(a standalone page; it works with JavaScript off and at phone width).

```
allow-list  ->  trust rule  ->  guard  ->  deterministic checks  ->  model  ->  policy
  (who)        (is it real)    (may it     (can code settle it)    (the rest)  (a row,
                                send)                                          never an act)
```

Each step can stop the next one. Roughly a third of the example messages are
settled by the deterministic checks with no model call at all.

## Safety properties, and the tests that hold them

These are not aspirations. Each one has a test that fails if it stops being
true.

| Property | Enforced by |
|---|---|
| Nothing in the package can drive a session. The source is parsed and rejected if it mentions pane keying, `send-keys`, `tmux` or similar. The check is AST aware, so prose may name them and code may not. | `tests/test_safety.py` |
| A fresh install classifies nothing. The allow-list ships empty. | `tests/test_config.py` |
| A deny entry outranks an allow entry, so a name in both is still refused. Matching is case-insensitive on both lists, so a differently cased allow entry cannot escape a deny entry. | `tests/test_config.py` |
| The guard blocks the send, not just the log. A spy transport asserts it is never called when the guard fires. | `tests/test_classify.py` |
| Message text never reaches the log. Rows are written through a field allow-list, so a caller cannot widen one by accident. | `tests/test_log.py` |
| Instructions inside a message are data. A message containing an injection attempt is recorded and can never be harvested, whatever the model answers and whatever else the message shows. | `tests/test_review_regressions.py` |
| No fallback to another model or endpoint, and a response from a different model id is refused. A failure is reported as a failure. | `tests/test_provider.py` |
| Model output never grants authority. A message that claims completion without evidence is never harvested, whatever the model chose. Evidence means a commit hash carrying both a digit and a hex letter, or a test-result line, not any long number. | `tests/test_review_regressions.py` |

## Install

Python 3.11 or newer. No required dependencies: the provider is standard
library only.

```bash
git clone https://github.com/vishalsachdev/harvest-classifier.git
cd harvest-classifier
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest
```

## Five minute offline quickstart

No API key, no network, nothing sent anywhere.

```bash
# 1. A config that opts one invented session in.
mkdir -p /tmp/hc-demo/status
cat > /tmp/hc-demo/config.toml <<'EOF'
[agents]
allow = ["build-agent"]
deny  = ["orchestrator"]
[status]
dir = "/tmp/hc-demo/status"
[log]
dir = "/tmp/hc-demo/log"
EOF

# 2. Two status files: one allow-listed, one not.
cat > /tmp/hc-demo/status/build-agent.json <<EOF
{"schema_version":1,"agent":"build-agent","session_id":"s1","state":"idle",
 "event":"Stop","updated_at":"$(date +%Y-%m-%dT%H:%M:%S%z)",
 "last_assistant_text":"Claude needs your permission to run this command.",
 "blocked_reason":null}
EOF
cat > /tmp/hc-demo/status/orchestrator.json <<EOF
{"schema_version":1,"agent":"orchestrator","session_id":"s2","state":"idle",
 "event":"Stop","updated_at":"$(date +%Y-%m-%dT%H:%M:%S%z)",
 "last_assistant_text":"Everything is fine.","blocked_reason":null}
EOF

# 3. Classify, with deterministic checks only.
.venv/bin/harvest-classifier --config /tmp/hc-demo/config.toml \
    shadow-harvest --once --dry-run

# 4. See exactly what would be sent to the model.
.venv/bin/harvest-classifier questions
```

Expected output:

```
build-agent            unblock              by=deterministic  guard=-

rows written: 1
not classifiable (allow-list): ['orchestrator']
```

Three things to notice. The orchestrator file was refused by the allow-list and
the run says so. The build-agent message was settled by the deterministic
checks with no model call, which is what `by=deterministic` means. And
`--dry-run` made no network call at all.

`--dry-run` only runs the deterministic stage, so anything it cannot settle
falls open to `ignore` rather than being guessed at. Drop the flag, with a key
set, to send the remainder to the model.

## Going live

The model stage uses TypeSafe's Jev through its HTTP API. The project is
independent of, and not endorsed by, TypeSafe AI. See <https://typesafe.ai> and
<https://docs.typesafe.ai>.

```bash
export TYPESAFE_API_KEY=...        # or set provider.api_key_file in the config
.venv/bin/harvest-classifier --config /tmp/hc-demo/config.toml shadow-harvest --once
```

The key is read at call time and is never stored on an object or printed.

**Cost.** One request per status change that neither the guard nor the
deterministic checks settle, carrying six yes/no questions and one choice. A
single observed request used 1,407 input tokens and took 350 ms, which at the
published input price is roughly six thousandths of a cent. That is one
request, not a benchmark: see [docs/RESULTS.md](docs/RESULTS.md). Output is not
billed, and messages over 20,000 characters are refused before any call.

The model id is pinned in configuration so a result can be attributed to a
specific model. Move it deliberately and re-run your fixtures when you do.

## Labelling and reading the report

The point of shadow mode is to find out whether the recommendation matches what
you actually did.

```bash
harvest-classifier shadow-label build-agent unblock --note "opened the dialog"
harvest-classifier shadow-report
```

The report gives the row count, the share settled without a model call, guard
blocks by category, total cost, and, once you have labelled some rows,
agreement and a confusion table.

## Limitations

* **No agreement number yet.** The classifier has almost no real labelled rows.
  Everything measured so far is on synthetic examples. See
  [docs/RESULTS.md](docs/RESULTS.md).
* **No thresholds are calibrated.** Every threshold in the policy is a first
  guess and every row says so.
* **It sees one message.** No task brief, no history, no knowledge of what the
  agent was asked to do, so "done" is judged against the message rather than
  against the assignment.
* **The guard is a second line, not the first.** Pattern rules miss things. The
  allow-list is the control that matters.
* **Confidence is not reliable enough to act on.** See
  [docs/RESULTS.md](docs/RESULTS.md).

## Documentation

* [docs/DESIGN.md](docs/DESIGN.md), the principles behind the shape.
* [docs/STATUS-FILE.md](docs/STATUS-FILE.md), the input contract.
* [docs/RESULTS.md](docs/RESULTS.md), what has and has not been measured.
* [SECURITY.md](SECURITY.md), what leaves the machine and when.
