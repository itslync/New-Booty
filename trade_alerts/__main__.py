"""CLI.

  python -m trade_alerts evaluate snapshot.json [--journal session.jsonl]
  python -m trade_alerts outcome  session.jsonl ID win|loss|scratch|not_triggered|invalidated [--r 1.8]
  python -m trade_alerts review   session.jsonl
  python -m trade_alerts update-stats session.jsonl stats.json [--write]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import journal
from .engine import ALERT, evaluate
from .models import SetupStats, Snapshot
from .report import format_decision


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="trade_alerts", description="SPY/QQQ intraday trade-alert gatekeeper")
    sub = p.add_subparsers(dest="cmd", required=True)

    ev = sub.add_parser("evaluate", help="evaluate candidates in a market snapshot")
    ev.add_argument("snapshot", type=Path)
    ev.add_argument("--journal", type=Path, help="append ALERT / LOG ONLY results to this JSONL file")

    oc = sub.add_parser("outcome", help="record the outcome of a journaled alert")
    oc.add_argument("journal", type=Path)
    oc.add_argument("id")
    oc.add_argument("outcome", choices=journal.OUTCOMES)
    oc.add_argument("--r", type=float, help="realized R multiple (required for win/loss/scratch)")

    rv = sub.add_parser("review", help="list every alert and its outcome")
    rv.add_argument("journal", type=Path)

    us = sub.add_parser("update-stats", help="fold decided outcomes into the setup stats table")
    us.add_argument("journal", type=Path)
    us.add_argument("stats", type=Path, help="JSON list of setup stats")
    us.add_argument("--write", action="store_true", help="overwrite the stats file instead of printing")

    a = p.parse_args(argv)

    if a.cmd == "evaluate":
        snap = Snapshot.from_dict(json.loads(a.snapshot.read_text()))
        decisions = evaluate(snap)
        print(f"As of {snap.as_of:%Y-%m-%d %H:%M} ET\n")
        print("\n\n".join(format_decision(d) for d in decisions))
        if a.journal:
            n = journal.append(a.journal, decisions, snap.as_of)
            print(f"\n[{n} new record(s) written to {a.journal}]")
        return 0 if any(d.status == ALERT for d in decisions) else 1

    if a.cmd == "outcome":
        r = journal.set_outcome(a.journal, a.id, a.outcome, a.r)
        print(f"{r['id']}: {r['outcome']} {'' if r['result_r'] is None else r['result_r']}")
        return 0

    if a.cmd == "review":
        print(journal.review(journal.load(a.journal)))
        return 0

    if a.cmd == "update-stats":
        stats = [SetupStats.from_dict(s) for s in json.loads(a.stats.read_text())]
        new = [s.to_dict() for s in journal.updated_stats(stats, journal.load(a.journal))]
        text = json.dumps(new, indent=2) + "\n"
        if a.write:
            a.stats.write_text(text)
        else:
            sys.stdout.write(text)
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
