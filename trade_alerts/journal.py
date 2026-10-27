"""Session journal: record alerts, record outcomes, review a session and fold results back into the stats table.

The journal is a JSON Lines file: one record per alert or log-only signal.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

from .engine import ALERT, LOG_ONLY, Decision
from .models import SetupStats

OUTCOMES = ("win", "loss", "scratch", "not_triggered", "invalidated")


def record_id(d: Decision) -> str:
    c = d.candidate
    slug = re.sub(r"[^a-z0-9]+", "-", c.setup.lower()).strip("-")
    return f"{c.expires:%Y%m%d}-{c.ticker}-{c.direction}-{slug}-{c.entry:g}"


def to_record(d: Decision, as_of) -> dict:
    c = d.candidate
    return {
        "id": record_id(d), "as_of": as_of.isoformat(), "status": d.status, "grade": d.grade,
        "ticker": c.ticker, "direction": c.direction, "setup": c.setup,
        "entry": c.entry, "stop": c.stop, "targets": [t.price for t in c.targets],
        "reward_risk": d.reward_risk, "score": d.score,
        "base_win_rate": d.base_win_rate, "adjusted_probability": d.adjusted_probability,
        "failed_confluence": [ch.name for ch in d.confluence if not ch.passed],
        "expires": d.expires.isoformat() if d.expires else None,
        "outcome": None, "result_r": None,
    }


def append(path: Path, decisions: list[Decision], as_of) -> int:
    """Append ALERT and LOG ONLY decisions; skip ids already recorded. Returns the number written."""
    existing = {r["id"] for r in load(path)}
    new = [to_record(d, as_of) for d in decisions if d.candidate and d.status in (ALERT, LOG_ONLY)]
    new = [r for r in new if r["id"] not in existing]
    with path.open("a") as f:
        for r in new:
            f.write(json.dumps(r) + "\n")
    return len(new)


def load(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def save(path: Path, records: list[dict]) -> None:
    path.write_text("".join(json.dumps(r) + "\n" for r in records))


def set_outcome(path: Path, record_id_: str, outcome: str, result_r: float | None) -> dict:
    if outcome not in OUTCOMES:
        raise ValueError(f"outcome must be one of {OUTCOMES}")
    if outcome in ("win", "loss", "scratch") and result_r is None:
        raise ValueError(f"'{outcome}' needs the realized R multiple (--r)")
    if outcome == "win" and result_r <= 0 or outcome == "loss" and result_r >= 0:
        raise ValueError(f"R of {result_r} contradicts outcome '{outcome}'")
    records = load(path)
    for r in records:
        if r["id"] == record_id_:
            r["outcome"], r["result_r"] = outcome, result_r
            save(path, records)
            return r
    raise KeyError(f"no journal record with id {record_id_}")


def review(records: list[dict]) -> str:
    """End-of-session list of every alert and its outcome, plus totals."""
    if not records:
        return "No alerts or logged signals this session."
    lines = []
    for r in records:
        outcome = r["outcome"] or "PENDING"
        res = f" {r['result_r']:+.2f}R" if r["result_r"] is not None else ""
        lines.append(f"{r['status']:<8} {r['grade']:<2} {r['ticker']} {r['direction']:<5} {r['setup']} "
                     f"@ {r['entry']:g} (score {r['score']}, adj {r['adjusted_probability']:.0%}) -> {outcome}{res}  [{r['id']}]")
    alerts = [r for r in records if r["status"] == ALERT]
    decided = [r for r in alerts if r["outcome"] in ("win", "loss")]
    wins = sum(r["outcome"] == "win" for r in decided)
    total_r = sum(r["result_r"] or 0 for r in alerts if r["outcome"] in ("win", "loss", "scratch"))
    lines.append("")
    lines.append(f"Alerts: {len(alerts)} | decided: {len(decided)} | wins: {wins} | net: {total_r:+.2f}R | "
                 f"pending: {sum(r['outcome'] is None for r in alerts)}")
    return "\n".join(lines)


def updated_stats(stats: list[SetupStats], records: list[dict]) -> list[SetupStats]:
    """Fold decided (win/loss) ALERT outcomes into the table. Scratches, no-triggers and log-only
    signals are excluded so the table keeps measuring the same thing it was backtested on."""
    by_setup: dict[str, list[float]] = defaultdict(list)
    for r in records:
        if r["status"] == ALERT and r["outcome"] in ("win", "loss"):
            by_setup[r["setup"]].append(r["result_r"])
    out = []
    for s in stats:
        results = by_setup.get(s.name, [])
        if not results:
            out.append(s)
            continue
        wins = [x for x in results if x > 0]
        losses = [x for x in results if x < 0]
        old_wins = s.win_rate * s.n
        old_losses = s.n - old_wins
        n = s.n + len(results)
        new_wins = old_wins + len(wins)
        new_losses = old_losses + len(losses)
        avg_win = (s.avg_win_r * old_wins + sum(wins)) / new_wins if new_wins else s.avg_win_r
        avg_loss = (s.avg_loss_r * old_losses + sum(losses)) / new_losses if new_losses else s.avg_loss_r
        out.append(SetupStats(s.name, new_wins / n, avg_win, avg_loss, n))
    return out
