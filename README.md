# SPY/QQQ Trade-Alert Gatekeeper

A deterministic rules engine for the intraday trade-alert playbook in [PROMPT.md](PROMPT.md).
It **suggests** trades and never executes them. The default output is `NO TRADE`.

A setup scanner (yours, or an LLM analyst using PROMPT.md) proposes **candidates**. The engine checks
each one against the playbook and returns `ALERT`, `LOG ONLY` or `NO TRADE`, with reasons. It never
invents setups or probabilities: the probability always comes from your SETUP STATS TABLE, adjusted
only by the confluence score.

Python 3.10+, standard library only.

## Usage

```bash
# Evaluate a snapshot (exit code 0 if at least one ALERT, 1 otherwise)
python -m trade_alerts evaluate examples/sample_snapshot.json --journal session.jsonl

# After the session: record each outcome in R multiples
python -m trade_alerts outcome session.jsonl <id> win --r 2.2
python -m trade_alerts outcome session.jsonl <id> not_triggered

# List every alert and its outcome
python -m trade_alerts review session.jsonl

# Fold decided outcomes into the stats table (prints; add --write to overwrite)
python -m trade_alerts update-stats session.jsonl examples/setup_stats.json

# Tests
python -m unittest discover -s tests -t .
```

## Pipeline

1. **Data checks** — any missing or stale input means `NO TRADE` for every candidate. Required: SPY
   and QQQ 1m/5m/1h/1d bars, VWAP, RVOL, all nine key levels (PDH, PDL, PDC, PMH, PML, ONH, ONL, WH, WL)
   plus at least one gamma/OI strike, TICK, ADD, VIX, the economic calendar and earnings (send `[]` for
   "none today" — omitting the key counts as missing), and the stats table.
   Staleness limits: 1m 3 min, 5m 10 min, 1h 75 min, internals 3 min, daily bar within 5 days and
   from a prior session.
2. **Setup gate** — the setup name must be in the stats table with n >= 100, and the ticker must be SPY or QQQ.
3. **Hard filters** (any = grade F, `NO TRADE`) — before 09:40 ET, 11:30–13:30 ET, after 16:00 ET;
   within ±30 min of a `CPI`/`FOMC`/`NFP`/`FED_CHAIR` event; SPY and QQQ last 5m bars moving in
   opposite directions; R:R to Target 1 below 1.5; stop not beyond the stated `stop_structure`
   level; time limit already passed.
4. **Confluence** (0–10) — computed from the data, not taken from the candidate.
5. **Grade** — adjusted probability = win rate +1%/point above 6, −2%/point below 6; then the grade
   ladder. C+ or better → `ALERT`; C/C- → `LOG ONLY`; D/E → `NO TRADE`.

The time limit is automatically clamped to the start of the next blackout window (lunch, news, close),
so an alert can never trigger inside a window where a fresh alert would be blocked.

## How each confluence item is measured

| # | Item | Rule |
|---|---|---|
| 1 | Daily trend | last daily close vs SMA(20) of daily closes |
| 2 | 1h trend | last 1h close vs SMA(20) of 1h closes |
| 3 | Key level | entry within 0.10% of any key level or gamma strike |
| 4 | VWAP | last 1m close above VWAP (long) / below (short) |
| 5 | RVOL | ticker RVOL >= 1.5 |
| 6 | TICK | latest TICK > +600 (long) / < −600 (short) |
| 7 | ADD | ADD higher (long) / lower (short) than its first reading in the last 15 min |
| 8 | VIX | VIX lower (long) / higher (short) than its first reading in the last 15 min |
| 9 | Both confirm | SPY **and** QQQ: last 5m bar moved with the trade and price on the trade's side of VWAP |
| 10 | Room to target | no key level strictly between entry and Target 1 (beyond the 0.10% tolerance) |

All thresholds live in [`trade_alerts/rules.py`](trade_alerts/rules.py).

## Choices the playbook left open

These are this implementation's interpretation; change them in `rules.py` if you disagree.

- **Grade sub-bands.** The playbook fixes the tier bands but not the +/- splits. Used here:
  A+ >= 91%, A 88–90%, A- 85–87%; B+ 84%, B 82–83%, B- 80–81%; C 72–74%, C- 70–71%.
- **Tiers are minimums.** A candidate gets the highest grade whose probability, score and R:R
  requirements are all met. E.g. 86% with score 8 is B+ (fails A's score 9 requirement).
- **R:R** for filters and grading is measured to Target 1, the conservative choice.
- **RVOL** is the ticker's current RVOL; send the trigger candle's RVOL in that field once it prints.
- **Outside regular hours** (before the open, after 16:00) is treated as a blackout.
- **Stats updates** fold only `ALERT` records with a `win` or `loss` outcome into the table.
  Scratches, no-triggers, invalidations and log-only signals are reported in the review but don't
  change the backtested numbers.

## Snapshot format

See [`examples/sample_snapshot.json`](examples/sample_snapshot.json) (generated from
`tests/fixtures.py`). Timestamps are ISO-8601; naive timestamps are read as US/Eastern.

```jsonc
{
  "as_of": "2026-10-27T10:15:00-04:00",
  "tickers": {
    "SPY": {
      "bars": {"1m": [{"t": "...", "o": 0, "h": 0, "l": 0, "c": 0, "v": 0}], "5m": [], "1h": [], "1d": []},
      "vwap": 579.5, "rvol": 1.8,
      "levels": {"PDH": 580.2, "PDL": 575.3, "PDC": 578.0, "PMH": 579.8, "PML": 577.5,
                 "ONH": 579.9, "ONL": 576.5, "WH": 584.0, "WL": 572.0, "gamma": [575, 585]}
    },
    "QQQ": { "...": "same shape" }
  },
  "internals": {"tick": [{"t": "...", "v": 750}], "add": [], "vix": []},
  "calendar": [{"time": "2026-10-27T14:00:00-04:00", "kind": "FOMC", "name": "FOMC statement"}],
  "earnings": [],
  "setup_stats": [{"name": "PDH breakout-retest", "win_rate": 0.82, "avg_win_r": 2.1, "avg_loss_r": -1.0, "n": 240}],
  "candidates": [{
    "ticker": "SPY", "direction": "long", "setup": "PDH breakout-retest",
    "entry": 580.25, "trigger": "5m close above 580.25",
    "stop": 579.00, "stop_structure": 579.30, "stop_reason": "below the 10:00 higher low",
    "targets": [{"price": 583.0, "reason": "below weekly high"}, {"price": 584.0, "reason": "weekly high"}],
    "invalidation": "5m close back below PDH", "expires": "2026-10-27T11:15:00-04:00"
  }]
}
```

Bars are oldest first. 1m and 5m bars must be completed bars; the last 1h bar may still be forming;
daily bars are prior completed sessions only.

> The sample data and stats table are synthetic, for demonstration only. Not financial advice.
