"""`harvest-classifier shadow-harvest | shadow-label | shadow-report`.

Shadow only. Every command reads status files and writes rows locally. No
command can act on an agent.
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import sys
import time
from datetime import datetime, timezone
from typing import Any

from . import log, provider, questions
from .classify import classify
from .config import Config, ConfigError, load_config
from .policy import ACTIONS
from . import status as status_module
from .status import dedupe_key, has_message, is_trustworthy, iter_status_files, read_status

LOG_NAME = "harvest-log.jsonl"
LABEL_NAME = "harvest-labels.jsonl"


def _sender(config: Config):
    """A callable taking the question payload and returning flat answers."""
    transport = provider.LiveTransport(config.read_api_key)

    def send(payload: dict[str, Any]) -> dict[str, Any]:
        text = payload["__state__"]
        question_set = {k: v for k, v in payload.items() if k != "__state__"}
        return provider.ask(transport, text, question_set, config.model)

    return send


def _candidates(config: Config) -> list[dict[str, Any]]:
    out = []
    for path in iter_status_files(config.status_dir):
        rec = read_status(path)
        if (rec and config.is_classifiable_record(rec)
                and is_trustworthy(rec, config.fresh_seconds) and has_message(rec)):
            out.append(rec)
    return out


def _resolve(args) -> tuple[Config, pathlib.Path, pathlib.Path]:
    config = load_config(args.config)
    log_dir = pathlib.Path(args.log_dir) if args.log_dir else config.log_dir
    return config, log_dir / LOG_NAME, log_dir / LABEL_NAME


def cmd_harvest(args) -> int:
    config, log_path, _ = _resolve(args)
    if not config.allow_list:
        print("no agents are allow-listed, so nothing will be classified.\n"
              "Add names under [agents].allow in your config file; see "
              "examples/config.example.toml.", file=sys.stderr)
        return 1

    send = None if args.dry_run else _sender(config)

    def sweep() -> tuple[int, int]:
        """Returns (rows written, provider failures).

        A provider failure is per record: one agent's 401 or pair of 429s must
        not end the sweep, and under --watch it must not end the loop
        (review, 2026-09-21).
        """
        seen = log.seen_keys(log_path)
        written = failures = 0
        for rec in _candidates(config):
            if dedupe_key(rec) in seen:
                continue
            try:
                row = classify(rec, send, config)
            except provider.ProviderError as exc:
                failures += 1
                print(f"{rec.get('agent', '?'):22s} provider failure, skipped: {exc}",
                      file=sys.stderr)
                continue
            if row is None:
                continue
            log.append(row, log_path)
            written += 1
            print(f"{row['agent']:22s} {row['recommendation']:20s} "
                  f"by={row['decided_by']:14s} guard={row['guard_categories'] or '-'}")
        return written, failures

    if args.once:
        total, failures = sweep()
        skipped = sorted({(read_status(p) or {}).get("agent") or p.stem
                          for p in iter_status_files(config.status_dir)
                          if not config.is_classifiable_record(read_status(p) or {})})
        print(f"\nrows written: {total}")
        if skipped:
            print(f"not classifiable (allow-list): {skipped}")
        if failures:
            print(f"provider failures: {failures}", file=sys.stderr)
            return 3
        return 0

    print(f"watching {config.status_dir} every {args.interval}s; ctrl-c to stop",
          file=sys.stderr)
    try:
        while True:
            sweep()
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return 0


def cmd_label(args) -> int:
    _, log_path, label_path = _resolve(args)
    rows = [r for r in log.read(log_path) if r.get("agent") == args.agent]
    if not rows:
        print(f"no classified row for {args.agent}", file=sys.stderr)
        return 1
    latest = rows[-1]
    entry = {
        "agent": args.agent,
        "session_id": latest.get("session_id"),
        "updated_at": latest.get("updated_at"),
        "text_fingerprint": latest.get("text_fingerprint"),
        "actual_action": args.action,
        "note": args.note,
        "labelled_at": datetime.now(timezone.utc).astimezone().isoformat(),
    }
    log.ensure_dir(label_path.parent)
    with open(label_path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, sort_keys=True) + "\n")
    label_path.chmod(log.FILE_MODE)
    print(f"labelled {args.agent} {latest.get('text_fingerprint')} -> {args.action}")
    return 0


def cmd_report(args) -> int:
    _, log_path, label_path = _resolve(args)
    rows = log.read(log_path)
    labels = []
    if label_path.exists():
        labels = [json.loads(l) for l in label_path.read_text().splitlines() if l.strip()]

    if not rows:
        print("no rows yet")
        return 0

    by_fp = {r.get("text_fingerprint"): r for r in rows}
    print(f"rows: {len(rows)}")
    print(f"  settled by deterministic checks alone: "
          f"{sum(1 for r in rows if r.get('decided_by') == 'deterministic')}")
    print(f"  guard blocked: {sum(1 for r in rows if r.get('guard_blocked'))}")
    blocked = collections.Counter(c for r in rows for c in r.get("guard_categories", []))
    if blocked:
        print(f"  guard categories: {dict(blocked)}")
    print(f"  model calls: {sum(1 for r in rows if r.get('jev_model'))}")
    print(f"  total cost: ${sum(r.get('usd') or 0 for r in rows):.5f}")
    print(f"  recommendations: {dict(collections.Counter(r['recommendation'] for r in rows))}")

    matched = [(by_fp[l["text_fingerprint"]], l) for l in labels
               if l.get("text_fingerprint") in by_fp]
    if not matched:
        print("\nno labels yet: run `shadow-label <agent> <action>` to record what you did")
        return 0

    agree = sum(1 for r, l in matched if r["recommendation"] == l["actual_action"])
    print(f"\nlabelled: {len(matched)}  agreement: {agree}/{len(matched)} "
          f"= {agree / len(matched):.3f}")
    table = collections.Counter((r["recommendation"], l["actual_action"])
                                for r, l in matched)
    print("\nconfusion (recommended -> actual):")
    for action in ACTIONS:
        row = [table.get((action, actual), 0) for actual in ACTIONS]
        if any(row):
            print(f"  {action:20s} " + " ".join(f"{n:3d}" for n in row))
    print("  " + " " * 20 + " " + " ".join(f"{a[:3]:>3s}" for a in ACTIONS))
    return 0


def cmd_questions(args) -> int:
    """Print the frozen question set, so a reader can see exactly what is sent."""
    print(json.dumps(questions.question_set(), indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="harvest-classifier",
        description="Shadow classifier for agent final messages. Recommends; never acts.")
    parser.add_argument("--config", help="path to a TOML config file")
    parser.add_argument("--log-dir", help="override the configured log directory")
    subs = parser.add_subparsers(dest="command", required=True)

    h = subs.add_parser("shadow-harvest", help="classify fresh allow-listed status files")
    mode = h.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true")
    mode.add_argument("--watch", action="store_true")
    h.add_argument("--interval", type=float, default=5.0)
    h.add_argument("--dry-run", action="store_true",
                   help="deterministic checks only; makes no network call")
    h.set_defaults(func=cmd_harvest)

    l = subs.add_parser("shadow-label", help="record what you actually did")
    l.add_argument("agent")
    l.add_argument("action", choices=list(ACTIONS))
    l.add_argument("--note", default="")
    l.set_defaults(func=cmd_label)

    r = subs.add_parser("shadow-report", help="agreement, confusion table, cost")
    r.set_defaults(func=cmd_report)

    q = subs.add_parser("questions", help="print the frozen question set")
    q.set_defaults(func=cmd_questions)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2
    except status_module.StatusDirError as exc:
        print(f"status directory refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
