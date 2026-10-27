"""Input data model and JSON parsing. Parsing never guesses: absent fields stay None and are reported later."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

from . import rules

ET = ZoneInfo(rules.ET)


def parse_time(value: str) -> datetime:
    """Parse an ISO-8601 timestamp; naive timestamps are taken as ET."""
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ET)
    return dt.astimezone(ET)


@dataclass(frozen=True)
class Bar:
    t: datetime  # bar start time
    o: float
    h: float
    l: float
    c: float
    v: float

    @classmethod
    def from_dict(cls, d: dict) -> "Bar":
        return cls(parse_time(d["t"]), float(d["o"]), float(d["h"]), float(d["l"]), float(d["c"]), float(d["v"]))


@dataclass(frozen=True)
class Reading:
    t: datetime
    v: float

    @classmethod
    def from_dict(cls, d: dict) -> "Reading":
        return cls(parse_time(d["t"]), float(d["v"]))


@dataclass
class TickerData:
    symbol: str
    # keys "1m", "5m", "1h", "1d", oldest first. 1m/5m: completed bars. 1h: the last may be the forming bar.
    # 1d: prior completed sessions only.
    bars: dict[str, list[Bar]]
    vwap: float | None
    rvol: float | None
    levels: dict[str, float]            # PDH, PDL, PDC, PMH, PML, ONH, ONL, WH, WL
    gamma_strikes: list[float]

    @classmethod
    def from_dict(cls, symbol: str, d: dict) -> "TickerData":
        bars = {tf: [Bar.from_dict(b) for b in d.get("bars", {}).get(tf, [])] for tf in ("1m", "5m", "1h", "1d")}
        levels_in = d.get("levels", {})
        levels = {k: float(v) for k, v in levels_in.items() if k != "gamma" and v is not None}
        gamma = [float(x) for x in levels_in.get("gamma", [])]
        return cls(symbol, bars, _opt_float(d.get("vwap")), _opt_float(d.get("rvol")), levels, gamma)

    @property
    def last_price(self) -> float | None:
        bars = self.bars.get("1m") or []
        return bars[-1].c if bars else None

    def all_levels(self) -> list[tuple[str, float]]:
        named = list(self.levels.items())
        return named + [(f"gamma {k:g}", k) for k in self.gamma_strikes]


@dataclass(frozen=True)
class Event:
    time: datetime
    kind: str   # CPI, FOMC, NFP, FED_CHAIR, or anything else (informational only)
    name: str

    @classmethod
    def from_dict(cls, d: dict) -> "Event":
        return cls(parse_time(d["time"]), str(d["kind"]).upper(), d.get("name", d["kind"]))


@dataclass(frozen=True)
class SetupStats:
    name: str
    win_rate: float      # 0..1
    avg_win_r: float
    avg_loss_r: float    # negative, e.g. -1.0
    n: int

    @classmethod
    def from_dict(cls, d: dict) -> "SetupStats":
        win_rate = float(d["win_rate"])
        if win_rate > 1:  # accept 78 as well as 0.78
            win_rate /= 100
        return cls(d["name"], win_rate, float(d["avg_win_r"]), float(d["avg_loss_r"]), int(d["n"]))

    def to_dict(self) -> dict:
        return {"name": self.name, "win_rate": round(self.win_rate, 4), "avg_win_r": round(self.avg_win_r, 3),
                "avg_loss_r": round(self.avg_loss_r, 3), "n": self.n}


@dataclass(frozen=True)
class Target:
    price: float
    reason: str


@dataclass
class Candidate:
    """A proposed trade from a setup scanner. The engine decides whether it is alert-worthy."""
    ticker: str
    direction: str           # "long" or "short"
    setup: str               # must match a SetupStats name exactly
    entry: float
    trigger: str             # e.g. "5m close above 581.20"
    stop: float
    stop_structure: float | None  # the structural level the stop must sit beyond
    stop_reason: str
    targets: list[Target]    # T1 first
    invalidation: str
    expires: datetime        # abandon if not triggered by this time

    @classmethod
    def from_dict(cls, d: dict) -> "Candidate":
        direction = str(d["direction"]).lower()
        if direction not in ("long", "short"):
            raise ValueError(f"direction must be long or short, got {d['direction']!r}")
        return cls(
            ticker=str(d["ticker"]).upper(),
            direction=direction,
            setup=d["setup"],
            entry=float(d["entry"]),
            trigger=d["trigger"],
            stop=float(d["stop"]),
            stop_structure=_opt_float(d.get("stop_structure")),
            stop_reason=d.get("stop_reason", ""),
            targets=[Target(float(t["price"]), t.get("reason", "")) for t in d.get("targets", [])],
            invalidation=d.get("invalidation", ""),
            expires=parse_time(d["expires"]),
        )

    @property
    def sign(self) -> int:
        return 1 if self.direction == "long" else -1


@dataclass
class Snapshot:
    as_of: datetime
    tickers: dict[str, TickerData]
    tick: list[Reading]
    add: list[Reading]
    vix: list[Reading]
    calendar: list[Event] | None       # None = not supplied (different from "no events today")
    earnings: list[dict] | None
    setup_stats: dict[str, SetupStats]
    candidates: list[Candidate] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "Snapshot":
        internals = d.get("internals", {})
        return cls(
            as_of=parse_time(d["as_of"]),
            tickers={s: TickerData.from_dict(s, td) for s, td in d.get("tickers", {}).items()},
            tick=[Reading.from_dict(r) for r in internals.get("tick", [])],
            add=[Reading.from_dict(r) for r in internals.get("add", [])],
            vix=[Reading.from_dict(r) for r in internals.get("vix", [])],
            calendar=None if d.get("calendar") is None else [Event.from_dict(e) for e in d["calendar"]],
            earnings=d.get("earnings"),
            setup_stats={s["name"]: SetupStats.from_dict(s) for s in d.get("setup_stats", [])},
            candidates=[Candidate.from_dict(c) for c in d.get("candidates", [])],
        )


def _opt_float(v) -> float | None:
    return None if v is None else float(v)
