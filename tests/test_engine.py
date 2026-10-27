from datetime import datetime
import tempfile
import unittest
from pathlib import Path

from trade_alerts import journal
from trade_alerts.engine import ALERT, LOG_ONLY, NO_TRADE, adjusted_probability, evaluate, grade
from trade_alerts.models import SetupStats, Snapshot
from trade_alerts.report import format_decision
from tests.fixtures import build_snapshot


def run(raw: dict):
    return evaluate(Snapshot.from_dict(raw))


def candidate(raw: dict) -> dict:
    return raw["candidates"][0]


class BaselineTest(unittest.TestCase):
    def test_clean_setup_alerts(self):
        [d] = run(build_snapshot())
        self.assertEqual(d.status, ALERT)
        self.assertEqual(d.score, 10)
        self.assertEqual(d.grade, "A-")
        self.assertAlmostEqual(d.adjusted_probability, 0.86)
        self.assertEqual(d.reward_risk, [2.2, 3.0])
        self.assertIn("A-", format_decision(d))

    def test_expiry_clamped_to_lunch_blackout(self):
        [d] = run(build_snapshot())
        self.assertEqual(f"{d.expires:%H:%M}", "11:30")

    def test_no_candidates_is_no_trade(self):
        raw = build_snapshot()
        raw["candidates"] = []
        [d] = run(raw)
        self.assertEqual(d.status, NO_TRADE)


class DataChecksTest(unittest.TestCase):
    def assertNoTrade(self, raw, fragment):
        [d] = run(raw)
        self.assertEqual(d.status, NO_TRADE)
        self.assertTrue(any(fragment in r for r in d.reasons), d.reasons)

    def test_missing_vix(self):
        raw = build_snapshot()
        raw["internals"]["vix"] = []
        self.assertNoTrade(raw, "VIX: missing")

    def test_stale_1m(self):
        raw = build_snapshot()
        raw["as_of"] = "2026-10-27T10:25:00-04:00"
        self.assertNoTrade(raw, "SPY 1m: stale")

    def test_calendar_not_supplied(self):
        raw = build_snapshot()
        del raw["calendar"]
        self.assertNoTrade(raw, "Economic calendar not supplied")

    def test_missing_level(self):
        raw = build_snapshot()
        del raw["tickers"]["QQQ"]["levels"]["PMH"]
        self.assertNoTrade(raw, "QQQ: key levels missing: PMH")

    def test_unknown_setup(self):
        raw = build_snapshot()
        candidate(raw)["setup"] = "Gut feel"
        self.assertNoTrade(raw, "not in the SETUP STATS TABLE")

    def test_small_sample(self):
        raw = build_snapshot()
        candidate(raw)["setup"] = "Opening range fade"
        self.assertNoTrade(raw, "n=64")

    def test_other_ticker_rejected(self):
        raw = build_snapshot()
        candidate(raw)["ticker"] = "IWM"
        self.assertNoTrade(raw, "SPY and QQQ only")


class HardFilterTest(unittest.TestCase):
    def assertF(self, raw, fragment):
        [d] = run(raw)
        self.assertEqual((d.status, d.grade), (NO_TRADE, "F"))
        self.assertTrue(any(fragment in r for r in d.reasons), d.reasons)

    def _shift_time(self, raw, hhmm):
        """Move every intraday timestamp so as_of lands on hhmm and nothing goes stale."""
        delta = datetime.fromisoformat(f"2026-10-27T{hhmm}:00-04:00") - datetime.fromisoformat(raw["as_of"])

        def shift(node):
            if isinstance(node, dict):
                return {k: (shift_ts(v) if k in ("t", "time", "as_of", "expires") else shift(v)) for k, v in node.items()}
            if isinstance(node, list):
                return [shift(x) for x in node]
            return node

        def shift_ts(v):
            dt = datetime.fromisoformat(v)
            return v if dt.hour == 16 else (dt + delta).isoformat()  # leave daily closes alone

        moved = shift(raw)
        candidate(moved)["expires"] = "2026-10-27T15:00:00-04:00"
        return moved

    def test_opening_blackout(self):
        self.assertF(self._shift_time(build_snapshot(), "09:35"), "first 10 min")

    def test_lunch_blackout(self):
        self.assertF(self._shift_time(build_snapshot(), "12:15"), "midday blackout")

    def test_news_window(self):
        raw = build_snapshot()
        raw["calendar"].append({"time": "2026-10-27T10:40:00-04:00", "kind": "FED_CHAIR", "name": "Fed Chair remarks"})
        self.assertF(raw, "within 30 min of Fed Chair remarks")

    def test_news_after_window_clamps_expiry(self):
        raw = build_snapshot()
        raw["calendar"].append({"time": "2026-10-27T11:00:00-04:00", "kind": "CPI", "name": "CPI"})
        [d] = run(raw)
        self.assertEqual(d.status, ALERT)
        self.assertEqual(f"{d.expires:%H:%M}", "10:30")

    def test_spy_qqq_divergence(self):
        raw = build_snapshot()
        last = raw["tickers"]["QQQ"]["bars"]["5m"][-1]
        last["c"] = raw["tickers"]["QQQ"]["bars"]["5m"][-2]["c"] - 0.3
        self.assertF(raw, "opposite directions")

    def test_low_reward_risk(self):
        raw = build_snapshot()
        candidate(raw)["targets"][0]["price"] = 581.80  # 1.24 R
        self.assertF(raw, "below 1.5:1")

    def test_stop_not_beyond_structure(self):
        raw = build_snapshot()
        candidate(raw)["stop"] = 579.40
        self.assertF(raw, "does not sit beyond")

    def test_stop_structure_required(self):
        raw = build_snapshot()
        candidate(raw)["stop_structure"] = None
        self.assertF(raw, "No structural level")

    def test_expired(self):
        raw = build_snapshot()
        candidate(raw)["expires"] = "2026-10-27T10:10:00-04:00"
        self.assertF(raw, "already passed")


class ConfluenceAndGradingTest(unittest.TestCase):
    def test_adjusted_probability(self):
        self.assertAlmostEqual(adjusted_probability(0.80, 6), 0.80)
        self.assertAlmostEqual(adjusted_probability(0.80, 9), 0.83)
        self.assertAlmostEqual(adjusted_probability(0.80, 4), 0.76)

    def test_grade_ladder(self):
        self.assertEqual(grade(0.86, 9, 2.2), ("A-", True))
        self.assertEqual(grade(0.86, 9, 1.8), ("B+", True))   # A needs R:R >= 2
        self.assertEqual(grade(0.86, 8, 2.5), ("B+", True))   # A needs score 9+
        self.assertEqual(grade(0.82, 7, 2.0), ("C+", True))   # B needs score 8+
        self.assertEqual(grade(0.77, 6, 2.0), ("C", False))   # C+ needs score 7+
        self.assertEqual(grade(0.65, 9, 2.0), ("D", False))
        self.assertEqual(grade(0.40, 9, 2.0), ("F", False))

    def test_failed_confluence_downgrades(self):
        raw = build_snapshot()
        raw["internals"]["tick"][0]["v"] = 200          # item 6 fails
        raw["tickers"]["SPY"]["rvol"] = 1.1             # item 5 fails
        raw["internals"]["vix"][-1]["v"] = 18.9         # item 8 fails
        [d] = run(raw)
        self.assertEqual(d.score, 7)
        self.assertAlmostEqual(d.adjusted_probability, 0.83)
        self.assertEqual((d.status, d.grade), (ALERT, "C+"))

    def test_log_only(self):
        raw = build_snapshot()
        candidate(raw)["setup"] = "VWAP reclaim"        # 74% base, score 10 -> 78% but ...
        raw["internals"]["tick"][0]["v"] = 200
        raw["tickers"]["SPY"]["rvol"] = 1.1
        raw["internals"]["vix"][-1]["v"] = 18.9
        raw["internals"]["add"][-1]["v"] = 800          # score 6 -> 74% -> C, log only
        [d] = run(raw)
        self.assertEqual(d.score, 6)
        self.assertEqual((d.status, d.grade), (LOG_ONLY, "C"))

    def test_level_in_the_way(self):
        raw = build_snapshot()
        raw["tickers"]["SPY"]["levels"]["gamma"].append(582)
        [d] = run(raw)
        self.assertFalse(d.confluence[9].passed)
        self.assertIn("gamma 582", d.confluence[9].detail)


class JournalTest(unittest.TestCase):
    def test_round_trip_and_stats_update(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "session.jsonl"
            snap = Snapshot.from_dict(build_snapshot())
            decisions = evaluate(snap)
            self.assertEqual(journal.append(path, decisions, snap.as_of), 1)
            self.assertEqual(journal.append(path, decisions, snap.as_of), 0)  # no duplicates

            rid = journal.load(path)[0]["id"]
            with self.assertRaises(ValueError):
                journal.set_outcome(path, rid, "win", -1.0)
            journal.set_outcome(path, rid, "win", 2.2)
            self.assertIn("win +2.20R", journal.review(journal.load(path)))

            stats = [SetupStats("PDH breakout-retest", 0.80, 2.0, -1.0, 100)]
            [s] = journal.updated_stats(stats, journal.load(path))
            self.assertEqual(s.n, 101)
            self.assertAlmostEqual(s.win_rate, 81 / 101)
            self.assertAlmostEqual(s.avg_win_r, (2.0 * 80 + 2.2) / 81)


if __name__ == "__main__":
    unittest.main()
