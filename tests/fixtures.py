"""Synthetic but internally consistent snapshot: a clean long on SPY at the prior-day high, 10:15 ET.

Every confluence item passes by construction, so tests can knock out one thing at a time.
"""

from datetime import date, datetime, timedelta

DAY = date(2026, 10, 27)


def _ts(d: date, h: int, m: int) -> str:
    return datetime(d.year, d.month, d.day, h, m).isoformat() + "-04:00"


def _bars(start_price: float, step: float, stamps: list[str], vol: float) -> list[dict]:
    out, p = [], start_price
    for t in stamps:
        out.append({"t": t, "o": p, "h": p + abs(step) + 0.05, "l": p - 0.05, "c": p + step, "v": vol})
        p += step
    return out


def _ticker(price: float, scale: float, entry_level: float) -> dict:
    daily_stamps = [_ts(DAY - timedelta(days=d), 0, 0)[:10] + "T16:00:00-04:00" for d in range(25, 0, -1)]
    hourly_stamps = []
    for d in range(4, 0, -1):
        hourly_stamps += [_ts(DAY - timedelta(days=d), h, 30) for h in range(9, 16)]
    hourly_stamps.append(_ts(DAY, 9, 30))
    return {
        "bars": {
            "1d": _bars(price - 20 * scale, 0.8 * scale, daily_stamps, 8e7),
            "1h": _bars(price - 8 * scale, 0.25 * scale, hourly_stamps[-25:], 6e6),
            "5m": _bars(price - 0.6 * scale, 0.25 * scale, [_ts(DAY, 10, 5), _ts(DAY, 10, 10)], 9e5),
            "1m": _bars(price - 0.1 * scale, 0.05 * scale, [_ts(DAY, 10, 13), _ts(DAY, 10, 14)], 2e5),
        },
        "vwap": price - 0.8 * scale,
        "rvol": 1.8,
        "levels": {
            "PDH": entry_level, "PDL": price - 5 * scale, "PDC": price - 2.3 * scale,
            "PMH": price - 0.5 * scale, "PML": price - 2.8 * scale,
            "ONH": price - 0.4 * scale, "ONL": price - 3.8 * scale,
            "WH": price + 3.7 * scale, "WL": price - 8.3 * scale,
            "gamma": [round(price - 5 * scale), round(price + 4.7 * scale)],
        },
    }


def build_snapshot() -> dict:
    internals_times = [_ts(DAY, 10, m) for m in (2, 6, 10, 14)]
    return {
        "as_of": _ts(DAY, 10, 15),
        "tickers": {
            "SPY": _ticker(580.30, 1.0, 580.20),
            "QQQ": _ticker(500.30, 0.86, 500.20),
        },
        "internals": {
            "tick": [{"t": internals_times[-1], "v": 750}],
            "add": [{"t": t, "v": v} for t, v in zip(internals_times, (900, 1050, 1180, 1300))],
            "vix": [{"t": t, "v": v} for t, v in zip(internals_times, (18.5, 18.3, 18.1, 17.9))],
        },
        "calendar": [{"time": _ts(DAY, 10, 0), "kind": "OTHER", "name": "Consumer Confidence"}],
        "earnings": [],
        "setup_stats": [
            {"name": "PDH breakout-retest", "win_rate": 0.82, "avg_win_r": 2.1, "avg_loss_r": -1.0, "n": 240},
            {"name": "VWAP reclaim", "win_rate": 0.74, "avg_win_r": 1.7, "avg_loss_r": -1.0, "n": 180},
            {"name": "Opening range fade", "win_rate": 0.81, "avg_win_r": 1.6, "avg_loss_r": -1.0, "n": 64},
        ],
        "candidates": [{
            "ticker": "SPY", "direction": "long", "setup": "PDH breakout-retest",
            "entry": 580.25, "trigger": "5m close above 580.25 after a retest that holds PDH 580.20",
            "stop": 579.00, "stop_structure": 579.30, "stop_reason": "below the 10:00 higher low (579.30)",
            "targets": [{"price": 583.00, "reason": "below weekly high 584.00"},
                        {"price": 584.00, "reason": "weekly high"}],
            "invalidation": "a 5m close back below PDH 580.20 before the trigger, or TICK prints below -600",
            "expires": _ts(DAY, 11, 45),
        }],
    }
