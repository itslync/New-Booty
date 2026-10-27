"""Human-readable output in the playbook's ALERT FORMAT."""

from __future__ import annotations

from .engine import ALERT, Decision


def format_decision(d: Decision) -> str:
    c = d.candidate
    if c is None:
        return "NO TRADE\n" + "\n".join(f"  - {r}" for r in d.reasons)

    header = f"{c.ticker} | {c.direction.upper()} | {c.setup} | {d.grade}"
    if d.status != ALERT:
        lines = [f"{d.status}: {header}"] + [f"  - {r}" for r in d.reasons]
        if d.score is not None:
            lines.append(f"  (score {d.score}/10, adj. prob {d.adjusted_probability:.0%})")
        return "\n".join(lines)

    lines = [
        header,
        f"Entry: {c.entry:.2f} — {c.trigger}",
        f"Stop: {c.stop:.2f} — {c.stop_reason} (beyond structure {c.stop_structure:.2f})",
    ]
    for i, t in enumerate(c.targets, 1):
        lines.append(f"Target {i}: {t.price:.2f} — {t.reason}")
    lines.append("R:R: " + " / ".join(f"T{i} {rr:.2f}:1" for i, rr in enumerate(d.reward_risk, 1)))
    lines.append(f"Backtested win rate: {d.base_win_rate:.0%} (n={d.sample_size}) | "
                 f"Adjusted probability: {d.adjusted_probability:.0%}")
    lines.append(f"Confluence: {d.score}/10")
    for i, ch in enumerate(d.confluence, 1):
        lines.append(f"  [{'x' if ch.passed else ' '}] {i:>2}. {ch.name} — {ch.detail}")
    lines.append(f"Invalidation: {c.invalidation}")
    limit = f"{d.expires:%H:%M} ET"
    if d.expires != c.expires:
        limit += f" (clamped from {c.expires:%H:%M} by the next blackout window)"
    lines.append(f"Time limit: abandon if not triggered by {limit}")
    return "\n".join(lines)
