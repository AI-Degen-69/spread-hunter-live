"""Blast-radius gate — reversibility decides, not confidence.

Order per guide §8: contained / wide / hard-to-reverse.

- CONTAINED (quoting — resting bids): lane opens easily. Checked on
  deterministic GREEN predicates only (pair_cost, both legs, diff scope).
- WIDE (sizing / stop-loss / shared inventory / fleet caps): lane opens
  only on deterministic checks + clean trajectory (no prior RED on same unit
  in this run). Confidence never opens it.
- HARD_TO_REVERSE (merge / redeem / complete — on-chain or spend):
  lane is CLOSED — not a high threshold, a closed lane. No evidence opens it.
  Human approval required at the graph's final Gate, outside this module.

Evidence order inside an open lane (guide §8):
  deterministic results → trajectory of this run → historical rollback rate → model assessment last.
Confidence is weakest and last; it never overrides a deterministic Red.

No LIVE calls. No venue reads. No signer construction.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class BlastRadius(str, Enum):
    CONTAINED = "contained"
    WIDE = "wide"
    HARD_TO_REVERSE = "hard_to_reverse"


# --- action → blast radius mapping (the Splitter's lane assignment) ---

# Contained: reversible, single-order, no chain write, cancel is pre-approved per safety.md
CONTAINED_ACTIONS = {"quote", "filter", "rank", "dedup", "cancel"}

# Wide: reversible but affects shared state (inventory skew, sizing ladder,
# fleet caps, stop-loss posture). Affects multiple markets / future cycles.
WIDE_ACTIONS = {"size", "sizing", "stop_loss", "hedge", "inventory_skew", "fleet_cap", "rerank"}

# Hard-to-reverse: on-chain mergePositions/redeemPositions or spending complete.
# Not a threshold — lane is closed per guide §8.
HARD_ACTIONS = {"merge", "redeem", "complete", "exit"}


def classify(action: str) -> BlastRadius:
    a = (action or "").strip().lower()
    if a in HARD_ACTIONS:
        return BlastRadius.HARD_TO_REVERSE
    if a in WIDE_ACTIONS:
        return BlastRadius.WIDE
    # Default contained — quoting/filter/rank are the demo's main lane.
    # Unknown actions don't silently become wide; explicit allowlist above
    # is what makes a unit wide. Fallback is contained, not wide.
    return BlastRadius.CONTAINED


@dataclass(frozen=True)
class Trajectory:
    """What happened to this unit earlier in THIS run."""
    prior_verdicts: tuple[str, ...] = ()  # e.g. ("RED: pair_cost 1.02", "GREEN")
    corrections_attempted: int = 0

    @property
    def is_clean(self) -> bool:
        # No prior RED in this run; one Red → trajectory is not clean.
        return all(v == "GREEN" for v in self.prior_verdicts) if self.prior_verdicts else True

    @property
    def red_count(self) -> int:
        return sum(1 for v in self.prior_verdicts if v != "GREEN")


@dataclass(frozen=True)
class Evidence:
    """Evidence in priority order per guide §8."""
    deterministic_pass: bool  # GREEN predicates all passed?
    deterministic_detail: str = ""  # which predicate failed, if any
    trajectory: Trajectory = field(default_factory=Trajectory)
    historical_rollback_rate: float | None = None  # 0..1, None = unknown
    model_assessment: str | None = None  # weakest; never overrides deterministic

    def order_key(self) -> tuple:
        # For audit logging: show that deterministic was read first.
        return (
            self.deterministic_pass,
            self.trajectory.is_clean,
            self.historical_rollback_rate,
            self.model_assessment,
        )


@dataclass(frozen=True)
class GateDecision:
    lane_open: bool
    verdict: str  # GREEN | RED | BLOCKED_CLOSED_LANE
    reason: str
    blast_radius: BlastRadius
    evidence: Evidence
    scope: str = ""  # what to fix if RED (single file/unit)


def evaluate(
    *,
    action: str,
    evidence: Evidence,
    scope: str = "",
) -> GateDecision:
    """Gate decision by blast radius.

    Returns BLOCKED_CLOSED_LANE for hard-to-reverse regardless of evidence —
    not a threshold, a closed lane. Caller (graph's final Gate) must route
    to human approval.
    """
    radius = classify(action)

    if radius == BlastRadius.HARD_TO_REVERSE:
        return GateDecision(
            lane_open=False,
            verdict="BLOCKED_CLOSED_LANE",
            reason=(
                f"hard-to-reverse lane closed for '{action}' — "
                "merge/redeem/complete require human at final Gate; "
                "not a threshold, lane does not open"
            ),
            blast_radius=radius,
            evidence=evidence,
            scope=scope,
        )

    if radius == BlastRadius.WIDE:
        # Wide: needs deterministic GREEN + clean trajectory.
        if not evidence.deterministic_pass:
            return GateDecision(
                lane_open=False,
                verdict="RED",
                reason=(
                    f"wide lane blocked for '{action}' — deterministic check failed: "
                    f"{evidence.deterministic_detail or 'RED'}; "
                    f"trajectory clean={evidence.trajectory.is_clean}"
                ),
                blast_radius=radius,
                evidence=evidence,
                scope=scope,
            )
        if not evidence.trajectory.is_clean:
            return GateDecision(
                lane_open=False,
                verdict="RED",
                reason=(
                    f"wide lane blocked for '{action}' — trajectory not clean "
                    f"({evidence.trajectory.red_count} prior RED); "
                    "needs human or rerun before widening shared state"
                ),
                blast_radius=radius,
                evidence=evidence,
                scope=scope,
            )
        # Historical rollback rate is informational now; doesn't open a Red.
        # Model assessment is last and weakest.
        return GateDecision(
            lane_open=True,
            verdict="GREEN",
            reason=f"wide lane open for '{action}' — deterministic GREEN + clean trajectory",
            blast_radius=radius,
            evidence=evidence,
            scope=scope,
        )

    # Contained: open on deterministic GREEN; Red stays Red (correction edge).
    if evidence.deterministic_pass:
        return GateDecision(
            lane_open=True,
            verdict="GREEN",
            reason=f"contained lane open for '{action}' — deterministic GREEN",
            blast_radius=radius,
            evidence=evidence,
            scope=scope,
        )
    return GateDecision(
        lane_open=False,
        verdict="RED",
        reason=(
            f"contained lane RED for '{action}' — {evidence.deterministic_detail or 'predicate failed'} "
            "(correction edge may retry single unit)"
        ),
        blast_radius=radius,
        evidence=evidence,
        scope=scope,
    )


# Convenience: what the graph logs per unit.
def lane_for_market(market: dict[str, Any] | None, action: str) -> BlastRadius:
    # Splitter uses this — dimension is blast_radius + condition_id, not folder.
    # market may carry an explicit blast_radius override for tests.
    if market and isinstance(market, dict) and market.get("_blast_radius"):
        try:
            return BlastRadius(str(market["_blast_radius"]))
        except ValueError:
            pass
    return classify(action)
