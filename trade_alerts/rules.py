"""Every tunable number in the playbook lives here, so the rules are auditable in one place."""

from datetime import time

TICKERS = ("SPY", "QQQ")
ET = "America/New_York"

# Session windows (ET).
SESSION_OPEN = time(9, 30)
SESSION_CLOSE = time(16, 0)
OPENING_BLACKOUT_MINUTES = 10
LUNCH_BLACKOUT = (time(11, 30), time(13, 30))

# News blackout: +/- this many minutes around these event kinds.
NEWS_KINDS = frozenset({"CPI", "FOMC", "NFP", "FED_CHAIR"})
NEWS_WINDOW_MINUTES = 30

# Trade geometry.
MIN_REWARD_RISK = 1.5
MIN_SAMPLE_SIZE = 100

# Confluence thresholds.
RVOL_MIN = 1.5
TICK_THRESHOLD = 600
TREND_SMA_LENGTH = 20
INTERNALS_LOOKBACK_MINUTES = 15  # window used to judge ADD / VIX direction
LEVEL_TOLERANCE_PCT = 0.0010     # entry within 0.10% of a level counts as "at" it

# Staleness limits: the newest item must be no older than this (minutes) at evaluation time.
STALE_MINUTES = {"1m": 3, "5m": 10, "1h": 75, "internals": 3}
DAILY_MAX_AGE_DAYS = 5           # last completed daily bar must be within this many calendar days
MIN_HISTORY = {"1m": 1, "5m": 2, "1h": TREND_SMA_LENGTH, "1d": TREND_SMA_LENGTH}

REQUIRED_LEVELS = ("PDH", "PDL", "PDC", "PMH", "PML", "ONH", "ONL", "WH", "WL")

# Probability adjustment around a neutral confluence score.
NEUTRAL_SCORE = 6
BONUS_PER_POINT = 0.01
PENALTY_PER_POINT = 0.02

# Grade ladder, checked top-down; the first row whose every requirement is met wins.
# (grade, min adjusted probability, min confluence score, min R:R, alertable)
# The playbook fixes the tier bands (A >= 85%, B 80-84%, C+ 75-79%, C/C- 70-74%, D, E, F);
# the +/- splits inside the A, B and C bands are this module's choice and are documented in README.
GRADE_LADDER = (
    ("A+", 0.91, 9, 2.0, True),
    ("A",  0.88, 9, 2.0, True),
    ("A-", 0.85, 9, 2.0, True),
    ("B+", 0.84, 8, MIN_REWARD_RISK, True),
    ("B",  0.82, 8, MIN_REWARD_RISK, True),
    ("B-", 0.80, 8, MIN_REWARD_RISK, True),
    ("C+", 0.75, 7, MIN_REWARD_RISK, True),
    ("C",  0.72, 0, MIN_REWARD_RISK, False),
    ("C-", 0.70, 0, MIN_REWARD_RISK, False),
    ("D",  0.60, 0, MIN_REWARD_RISK, False),
    ("E",  0.50, 0, MIN_REWARD_RISK, False),
)
FAIL_GRADE = "F"
