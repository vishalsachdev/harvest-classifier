<!-- audience: external -->
# Changelog

## 0.1.0

First public release. Shadow only.

* Allow-list and deny-list gate, empty by default, deny outranks allow.
* Freshness and trust rule over status files, with the input contract
  documented in `docs/STATUS-FILE.md`.
* Pre-send guard for email addresses, token shaped strings, a configurable
  institutional identifier pattern, a name near a grade or score, and
  configurable private path prefixes. Blocks the send, records categories only.
* Deterministic checks covering commit hashes, passing and failing test lines,
  questions, owner decisions, permission dialogs, command output, deferred work
  and embedded instructions.
* Frozen question set: six yes/no questions and one choice over five actions,
  guarded by a digest test.
* Pure code policy combining the two, with every threshold marked provisional.
* CLI: `shadow-harvest`, `shadow-label`, `shadow-report`, `questions`.
* 36 synthetic fixtures including adversarial shapes.
* Configuration via TOML. No required dependencies.
