"""Is the near-miss evidence strong enough to license a gate trial? (#87)

The ranker already writes the evidence: one JSONL line per rank in
`runtime/near_misses.jsonl` (depth rejects the allocator would have funded) and
`runtime/volume_near_misses.jsonl` (every volume reject carrying a measured
volume). Nothing read it back, so the evidence accumulated and was discarded --
the live dashboard could show that markets were being refused, but never that
they were being refused CONSISTENTLY, which is the precondition for changing a
bar on purpose instead of on a hunch.

Readiness is four questions, and all four have to answer yes:

* **days** -- has the population been observed across enough distinct days that
  it is not one afternoon's quirk?
* **unique markets** -- is it many markets, or the same three re-scanned?
* **small margins** -- did enough of them miss the bar by a little rather than
  by an order of magnitude? A market at half the bar is evidence about the bar;
  one at a hundredth of it is evidence about the market.
* **stability** -- did the population show up in most ranks, or in a burst?

Readiness is not profitability. It says the evidence is worth running a
controlled trial on; the trial measures whether the change pays.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

# The sim's `?view=scan` thresholds, carried over unchanged so the two
# dashboards license a trial on the same evidence.
NEAR_MISS_MIN_DAYS: float = 3.0
MIN_UNIQUE: int = 25
MIN_SMALL_MARGIN: int = 5
MIN_STABILITY: float = 0.5
LOOKBACK_RANKS: int = 72

# "Missed by a little": the measured value reached at least half the bar.
SMALL_MARGIN_FRACTION: float = 0.5


@dataclass(frozen=True)
class Tracker:
    """One gate's readiness reading."""

    gate: str
    ranks: int = 0
    days: float = 0.0
    unique_markets: int = 0
    small_margin: int = 0
    stability: float = 0.0
    population: int = 0
    unparsed: int = 0
    ready: bool = False
    blockers: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        return {
            "gate": self.gate,
            "ranks": self.ranks,
            "days": round(self.days, 2),
            "unique_markets": self.unique_markets,
            "small_margin": self.small_margin,
            "stability": round(self.stability, 3),
            "population": self.population,
            "unparsed": self.unparsed,
            "ready": self.ready,
            "blockers": list(self.blockers),
            "thresholds": {
                "min_days": NEAR_MISS_MIN_DAYS,
                "min_unique": MIN_UNIQUE,
                "min_small_margin": MIN_SMALL_MARGIN,
                "min_stability": MIN_STABILITY,
                "lookback_ranks": LOOKBACK_RANKS,
            },
        }


def read_ranks(path: Path | str, lookback: int = LOOKBACK_RANKS) -> list[dict]:
    """The last `lookback` rank lines, oldest first.

    A file that does not exist, cannot be read, or holds a truncated last line
    is not an error: the tracker reports what it has. A half-written line from
    a ranker that is mid-append must never take the dashboard down.
    """
    p = Path(path)
    try:
        raw = p.read_text(encoding="utf-8").splitlines()
    except (OSError, ValueError):
        return []
    rows: list[dict] = []
    for line in raw[-max(1, lookback):]:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _span_days(timestamps: Iterable[float]) -> float:
    stamps = [t for t in timestamps if t]
    if len(stamps) < 2:
        return 0.0
    return (max(stamps) - min(stamps)) / 86400.0


def _tracker(gate: str, ranks: list[dict], entries_key: str,
             measured_key: str, bar_key: str,
             unparsed_key: str) -> Tracker:
    if not ranks:
        return Tracker(gate=gate, blockers=("no evidence recorded yet",))

    unique: set[str] = set()
    small_margin = 0
    population = 0
    unparsed = 0
    ranks_with_population = 0
    stamps: list[float] = []

    for rank in ranks:
        try:
            stamps.append(float(rank.get("ts") or 0.0))
        except (TypeError, ValueError):
            pass
        try:
            unparsed += int(rank.get(unparsed_key) or 0)
        except (TypeError, ValueError):
            pass
        entries = rank.get(entries_key) or []
        if not isinstance(entries, list):
            continue
        if entries:
            ranks_with_population += 1
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            population += 1
            cid = entry.get("cid")
            if cid:
                unique.add(str(cid))
            try:
                measured = float(entry.get(measured_key))
                bar = float(entry.get(bar_key))
            except (TypeError, ValueError):
                continue
            # Missed by a little, not by an order of magnitude. A market at a
            # hundredth of the bar says nothing about where the bar belongs.
            if bar > 0 and measured >= bar * SMALL_MARGIN_FRACTION:
                small_margin += 1

    days = _span_days(stamps)
    stability = ranks_with_population / len(ranks) if ranks else 0.0

    blockers: list[str] = []
    if days < NEAR_MISS_MIN_DAYS:
        blockers.append(f"observed over {days:.1f}d, needs {NEAR_MISS_MIN_DAYS:.0f}d")
    if len(unique) < MIN_UNIQUE:
        blockers.append(f"{len(unique)} unique markets, needs {MIN_UNIQUE}")
    if small_margin < MIN_SMALL_MARGIN:
        blockers.append(f"{small_margin} small-margin misses, needs {MIN_SMALL_MARGIN}")
    if stability < MIN_STABILITY:
        blockers.append(f"present in {stability:.0%} of ranks, needs "
                        f"{MIN_STABILITY:.0%}")

    return Tracker(
        gate=gate,
        ranks=len(ranks),
        days=days,
        unique_markets=len(unique),
        small_margin=small_margin,
        stability=stability,
        population=population,
        unparsed=unparsed,
        ready=not blockers,
        blockers=tuple(blockers),
    )


def depth_tracker(path: Path | str, lookback: int = LOOKBACK_RANKS) -> Tracker:
    """Readiness of the DEPTH bar, from the would-fund near-miss log."""
    return _tracker("depth", read_ranks(path, lookback), "greens",
                    "depth_measured", "depth_bar", "depth_unparsed")


def volume_tracker(path: Path | str, lookback: int = LOOKBACK_RANKS) -> Tracker:
    """Readiness of the VOLUME bar, from the volume-reject log."""
    return _tracker("volume", read_ranks(path, lookback), "volumes",
                    "volume_measured", "volume_bar", "volume_unknown")


def readiness(depth_path: Path | str, volume_path: Path | str,
              lookback: int = LOOKBACK_RANKS,
              now: float | None = None) -> dict[str, Any]:
    """Both trackers plus the banner flag the screener renders.

    `trial_ready` is true when EITHER gate has enough evidence: a trial changes
    one bar at a time, and the banner's job is to say that one is available.
    """
    depth = depth_tracker(depth_path, lookback)
    volume = volume_tracker(volume_path, lookback)
    ready_gates = [t.gate for t in (depth, volume) if t.ready]
    return {
        "generated_ts": time.time() if now is None else now,
        "depth": depth.as_dict(),
        "volume": volume.as_dict(),
        "trial_ready": bool(ready_gates),
        "ready_gates": ready_gates,
    }
