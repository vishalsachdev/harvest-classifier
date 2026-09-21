<!-- audience: external -->
# Design

Five principles. They explain most of the code's shape, including the parts
that look like extra work.

## Models propose, deterministic policy decides, existing tools execute

The model answers questions. It does not choose what happens. The policy that
turns answers into a recommendation is ordinary code you can read, test and
argue with, and the recommendation is a string in a log file that a person acts
on.

This is why there is no `--execute` flag and no plan to add one. A flag "for
later" is a commitment made before the evidence exists.

## Deterministic facts beat model inference

If code can settle it, code settles it, before the model is asked anything. A
commit hash, a passing or failing test line, a trailing question mark, a
permission dialog, the session's own `blocked` state: all of these are checked
first, and several of them settle the recommendation outright.

This is not only about cost. A regular expression that finds `128 passed` is
right every time, and a model asked the same question is right most of the
time. Spend the model on what code cannot do.

## Escalation is an ordinary outcome

`escalate_to_owner`, `bounce_for_evidence` and `unblock` are not failures of
the classifier. They are correct answers, and the policy prefers them when the
evidence is thin. A classifier that avoids escalating looks better in a demo
and is worse in use.

## Provider signals are not interchangeable

There is no universal `confidence` abstraction here. A yes/no question returns
a probability and nothing else. A choice returns a selected option, a
distribution and a concentration measure. A different provider would mean
something different again. Raw provider semantics are stored and the policy
interprets them explicitly, per question type.

Where a value is absent, it stays absent rather than being read as a zero. A
response missing an answer, or carrying a null where a probability belongs, is
refused as malformed rather than flowing onward as a confident `ignore` that
deduplication would never retry.

## Confidence is not permission

A high number is a statement about a distribution, not a warrant. No confidence
value may promote an action, waive a check, or turn a claim into evidence. The
concrete expression of this: a message claiming completion with no supporting
evidence is never harvested, at any confidence the model reports.

## Two consequences worth naming

**The guard blocks the send, not the log.** Recording that a message was
sensitive after sending it is not a safety property. The guard runs before the
request is built, and a test injects a spy sender to prove it is never called
on a guard hit. `tests/test_provider.py` exercises the transport itself with a
counting fake, covering the retry budget, the refusal to retry a client error,
and the refusal to accept an answer from a different model id.

**Text inside a message is data.** Agent output routinely contains instructions,
sometimes deliberately. The questions say so explicitly, and an injected
instruction is recorded as a flag and otherwise ignored.
