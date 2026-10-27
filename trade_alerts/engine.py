"""Decision engine: data checks -> hard filters -> confluence score -> grade -> ALERT / LOG / NO TRADE.

The default answer is NO TRADE. A candidate only becomes an ALERT when every data check and hard
filter passes and the grade is C+ or better.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from . import rules
from .models import Candidate, Reading, Snapshot, TickerData

NO_TRADE = "NO TRADE"
LOG_ONLY = "LOG ONLY"
ALERT = "ALERT"

CONFLUENCE_ITEMS = (
    "Aligns with daily trend",
    "Aligns with 1h trend",
    "Entry at a key level",
    "Correct side of VWAP",
    f"RVOL >= {rules.RVOL_MIN}",
    f"TICK confirms (beyond +/-{rules.TICK_THRESHOLD})",
    "ADD trending with the trade",
    "VIX moving opposite to the trade",
    "SPY and QQQ both confirm",
    "Clear room to Target 1",
)


@dataclass
class Check:
    name: str
    passed: bool
    detail: str


@dataclass
class Decision:
    candidate: Candidate | None
    status: str = NO_TRADE
    grade: str = rules.FAIL_GRADE
    reasons: list[str] = field(default_factory=list)       # why it is not an alert (empty for alerts)
    confluence: list[Check] = field(default_factory=list)
    score: int | None = None
    base_win_rate: float | None = None
    sample_size: int | None = None
    adjusted_probability: float | None = None
    reward_risk: list[float] = field(default_factory=list)  # one per target
    expires: datetime | None = None                         # effective time limit after blackout clamping


# --------------------------------------------------------------------------- data checks

def data_problems(snap: Snapshot) -> list[str]:
    """Every missing or stale input. Any entry here means NO TRADE for every candidate."""
    problems: list[str] = []
    now = snap.as_of

    for sym in rules.TICKERS:
        td = snap.tickers.get(sym)
        if td is None:
            problems.append(f"{sym}: no data supplied")
            continue
        for tf, min_len in rules.MIN_HISTORY.items():
            bars = td.bars.get(tf, [])
            if len(bars) < min_len:
                problems.append(f"{sym} {tf}: need >= {min_len} bars, got {len(bars)}")
                continue
            if tf == "1d":
                age_days = (now.date() - bars[-1].t.date()).days
                if age_days < 1:
                    problems.append(f"{sym} 1d: last daily bar is today's (must be prior completed sessions only)")
                elif age_days > rules.DAILY_MAX_AGE_DAYS:
                    problems.append(f"{sym} 1d: last daily bar is {age_days} days old")
            else:
                _check_age(problems, f"{sym} {tf}", bars[-1].t, now, rules.STALE_MINUTES[tf])
        if td.vwap is None:
            problems.append(f"{sym}: VWAP missing")
        if td.rvol is None:
            problems.append(f"{sym}: RVOL missing")
        missing_levels = [k for k in rules.REQUIRED_LEVELS if k not in td.levels]
        if missing_levels:
            problems.append(f"{sym}: key levels missing: {', '.join(missing_levels)}")
        if not td.gamma_strikes:
            problems.append(f"{sym}: gamma/OI strikes missing")

    for name, series in (("TICK", snap.tick), ("ADD", snap.add), ("VIX", snap.vix)):
        if not series:
            problems.append(f"{name}: missing")
        else:
            _check_age(problems, name, series[-1].t, now, rules.STALE_MINUTES["internals"])
    for name, series in (("ADD", snap.add), ("VIX", snap.vix)):
        if series and len(_window(series, now, rules.INTERNALS_LOOKBACK_MINUTES)) < 2:
            problems.append(f"{name}: need >= 2 readings in the last {rules.INTERNALS_LOOKBACK_MINUTES} min to judge direction")

    if snap.calendar is None:
        problems.append("Economic calendar not supplied (send [] if there are no events)")
    if snap.earnings is None:
        problems.append("Earnings calendar not supplied (send [] if there are none)")
    if not snap.setup_stats:
        problems.append("SETUP STATS TABLE missing")
    return problems


def _check_age(problems: list[str], label: str, t: datetime, now: datetime, limit_min: int) -> None:
    age = (now - t).total_seconds() / 60
    if age > limit_min:
        problems.append(f"{label}: stale (last update {age:.0f} min ago, limit {limit_min} min)")
    elif age < -1:
        problems.append(f"{label}: timestamp is in the future relative to as_of")


def _window(series: list[Reading], now: datetime, minutes: int) -> list[Reading]:
    start = now - timedelta(minutes=minutes)
    return [r for r in series if start <= r.t <= now]


# --------------------------------------------------------------------------- time helpers

def _at(now: datetime, t) -> datetime:
    return now.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)


def blackout_windows(snap: Snapshot) -> list[tuple[datetime, datetime, str]]:
    now = snap.as_of
    open_ = _at(now, rules.SESSION_OPEN)
    windows = [
        (now.replace(hour=0, minute=0, second=0, microsecond=0), open_ + timedelta(minutes=rules.OPENING_BLACKOUT_MINUTES),
         f"before {rules.SESSION_OPEN:%H:%M} open or within the first {rules.OPENING_BLACKOUT_MINUTES} min"),
        (_at(now, rules.LUNCH_BLACKOUT[0]), _at(now, rules.LUNCH_BLACKOUT[1]),
         f"{rules.LUNCH_BLACKOUT[0]:%H:%M}-{rules.LUNCH_BLACKOUT[1]:%H:%M} ET midday blackout"),
        (_at(now, rules.SESSION_CLOSE), now.replace(hour=23, minute=59, second=59, microsecond=0), "after the close"),
    ]
    pad = timedelta(minutes=rules.NEWS_WINDOW_MINUTES)
    for ev in snap.calendar or []:
        if ev.kind in rules.NEWS_KINDS:
            windows.append((ev.time - pad, ev.time + pad,
                            f"within {rules.NEWS_WINDOW_MINUTES} min of {ev.name} at {ev.time:%H:%M} ET"))
    return windows


# --------------------------------------------------------------------------- market helpers

def _sma(values: list[float], n: int) -> float:
    return sum(values[-n:]) / n


def trend(td: TickerData, tf: str) -> int:
    """+1 if the last close is above its SMA(n), -1 if below, 0 if exactly on it."""
    closes = [b.c for b in td.bars[tf]]
    diff = closes[-1] - _sma(closes, rules.TREND_SMA_LENGTH)
    return (diff > 0) - (diff < 0)


def five_minute_direction(td: TickerData) -> int:
    bars = td.bars["5m"]
    diff = bars[-1].c - bars[-2].c
    return (diff > 0) - (diff < 0)


def _series_direction(series: list[Reading], now: datetime) -> int:
    w = _window(series, now, rules.INTERNALS_LOOKBACK_MINUTES)
    diff = w[-1].v - w[0].v
    return (diff > 0) - (diff < 0)


def reward_risk(c: Candidate) -> list[float]:
    risk = (c.entry - c.stop) * c.sign
    return [round((t.price - c.entry) * c.sign / risk, 2) for t in c.targets] if risk > 0 else []


# --------------------------------------------------------------------------- hard filters

def hard_filter_violations(snap: Snapshot, c: Candidate) -> tuple[list[str], datetime | None]:
    """Returns (violations, effective expiry). Expiry is clamped to the next blackout start."""
    v: list[str] = []
    now = snap.as_of

    for start, end, why in blackout_windows(snap):
        if start <= now < end:
            v.append(f"Time filter: {why}")

    spy, qqq = five_minute_direction(snap.tickers["SPY"]), five_minute_direction(snap.tickers["QQQ"])
    if spy * qqq < 0:
        v.append(f"SPY and QQQ moving in opposite directions on the 5m (SPY {_arrow(spy)}, QQQ {_arrow(qqq)})")

    if (c.entry - c.stop) * c.sign <= 0:
        v.append(f"Stop {c.stop} is not on the losing side of entry {c.entry} for a {c.direction}")
    if not c.targets:
        v.append("No targets supplied")
    elif any((t.price - c.entry) * c.sign <= 0 for t in c.targets):
        v.append("A target is not on the winning side of entry")
    rr = reward_risk(c)
    if rr and rr[0] < rules.MIN_REWARD_RISK:
        v.append(f"Reward-to-risk to Target 1 is {rr[0]:.2f}:1, below {rules.MIN_REWARD_RISK}:1")

    if c.stop_structure is None:
        v.append("No structural level given for the stop")
    elif (c.stop_structure - c.stop) * c.sign <= 0:
        v.append(f"Stop {c.stop} does not sit beyond the structural level {c.stop_structure}")

    expires = c.expires
    for start, _end, _why in blackout_windows(snap):
        if now < start < expires:
            expires = start
    if expires <= now:
        v.append(f"Time limit {c.expires:%H:%M} ET has already passed")
    return v, expires


def _arrow(d: int) -> str:
    return {1: "up", -1: "down", 0: "flat"}[d]


# --------------------------------------------------------------------------- confluence

def confluence(snap: Snapshot, c: Candidate) -> list[Check]:
    td = snap.tickers[c.ticker]
    now, s = snap.as_of, c.sign
    price = td.last_price
    tol = c.entry * rules.LEVEL_TOLERANCE_PCT
    levels = td.all_levels()
    checks: list[Check] = []

    d_trend, h_trend = trend(td, "1d"), trend(td, "1h")
    checks.append(Check(CONFLUENCE_ITEMS[0], d_trend == s, f"daily close vs SMA{rules.TREND_SMA_LENGTH}: {_arrow(d_trend)}"))
    checks.append(Check(CONFLUENCE_ITEMS[1], h_trend == s, f"1h close vs SMA{rules.TREND_SMA_LENGTH}: {_arrow(h_trend)}"))

    near = [(n, p) for n, p in levels if abs(p - c.entry) <= tol]
    checks.append(Check(CONFLUENCE_ITEMS[2], bool(near),
                        ", ".join(f"{n} {p:g}" for n, p in near) if near else f"no level within {tol:.2f} of entry"))

    vwap_ok = (price - td.vwap) * s > 0
    checks.append(Check(CONFLUENCE_ITEMS[3], vwap_ok, f"last {price:g} vs VWAP {td.vwap:g}"))

    checks.append(Check(CONFLUENCE_ITEMS[4], td.rvol >= rules.RVOL_MIN, f"RVOL {td.rvol:g}"))

    tick = snap.tick[-1].v
    checks.append(Check(CONFLUENCE_ITEMS[5], tick * s > rules.TICK_THRESHOLD, f"TICK {tick:+g}"))

    add_dir = _series_direction(snap.add, now)
    checks.append(Check(CONFLUENCE_ITEMS[6], add_dir == s, f"ADD {_arrow(add_dir)} over {rules.INTERNALS_LOOKBACK_MINUTES} min"))

    vix_dir = _series_direction(snap.vix, now)
    checks.append(Check(CONFLUENCE_ITEMS[7], vix_dir == -s, f"VIX {_arrow(vix_dir)} over {rules.INTERNALS_LOOKBACK_MINUTES} min"))

    both = []
    for sym in rules.TICKERS:
        other = snap.tickers[sym]
        ok = five_minute_direction(other) == s and (other.last_price - other.vwap) * s > 0
        both.append(f"{sym} {'ok' if ok else 'no'}")
    checks.append(Check(CONFLUENCE_ITEMS[8], all(b.endswith("ok") for b in both),
                        "5m direction + VWAP side: " + ", ".join(both)))

    if c.targets:
        t1 = c.targets[0].price
        lo, hi = sorted((c.entry, t1))
        blocking = [(n, p) for n, p in levels if lo + tol < p < hi - tol]
        checks.append(Check(CONFLUENCE_ITEMS[9], not blocking,
                            "in the way: " + ", ".join(f"{n} {p:g}" for n, p in blocking) if blocking else "path to T1 clear"))
    else:
        checks.append(Check(CONFLUENCE_ITEMS[9], False, "no target"))
    return checks


# --------------------------------------------------------------------------- grading

def adjusted_probability(win_rate: float, score: int) -> float:
    delta = score - rules.NEUTRAL_SCORE
    adj = win_rate + (delta * rules.BONUS_PER_POINT if delta > 0 else delta * rules.PENALTY_PER_POINT)
    return min(max(adj, 0.0), 1.0)


def grade(prob: float, score: int, rr: float) -> tuple[str, bool]:
    # Round to whole percent before comparing so 0.8499999 doesn't miss the 85% band.
    p = round(prob * 100) / 100
    for name, min_p, min_score, min_rr, alertable in rules.GRADE_LADDER:
        if p >= min_p and score >= min_score and rr >= min_rr:
            return name, alertable
    return rules.FAIL_GRADE, False


# --------------------------------------------------------------------------- entry point

def evaluate(snap: Snapshot) -> list[Decision]:
    """One decision per candidate. With no candidates, a single NO TRADE decision explains the state."""
    problems = data_problems(snap)
    if not snap.candidates:
        return [Decision(None, reasons=problems or ["No candidate setups supplied"])]
    return [_evaluate_one(snap, c, problems) for c in snap.candidates]


def _evaluate_one(snap: Snapshot, c: Candidate, problems: list[str]) -> Decision:
    d = Decision(c)
    if c.ticker not in rules.TICKERS:
        d.reasons.append(f"{c.ticker} is not tradeable; SPY and QQQ only")
        return d
    stats = snap.setup_stats.get(c.setup)
    if stats is None:
        d.reasons.append(f"Setup '{c.setup}' is not in the SETUP STATS TABLE")
    elif stats.n < rules.MIN_SAMPLE_SIZE:
        d.reasons.append(f"Setup '{c.setup}' has n={stats.n}, below the minimum {rules.MIN_SAMPLE_SIZE}")
    if problems:
        d.reasons.extend(problems)
    if d.reasons:
        return d  # can't trust anything downstream

    d.base_win_rate, d.sample_size = stats.win_rate, stats.n
    d.reward_risk = reward_risk(c)
    violations, d.expires = hard_filter_violations(snap, c)
    d.confluence = confluence(snap, c)
    d.score = sum(ch.passed for ch in d.confluence)
    d.adjusted_probability = adjusted_probability(stats.win_rate, d.score)

    if violations:
        d.reasons.extend(violations)
        return d  # grade F, NO TRADE

    d.grade, alertable = grade(d.adjusted_probability, d.score, d.reward_risk[0])
    if alertable:
        d.status = ALERT
    else:
        d.status = LOG_ONLY if d.grade in ("C", "C-") else NO_TRADE
        d.reasons.append(f"Grade {d.grade} is below the C+ alert minimum "
                         f"(adj. prob {d.adjusted_probability:.0%}, score {d.score}/10, R:R {d.reward_risk[0]:.2f})")
    return d
