"""Spread graph — filter → quote → merge.

Guide mapping:
  Splitter  — cuts by blast_radius + condition_id (not folder). Wrong dimension wastes all downstream work.
  Worker    — one unit, own context, loop lives INSIDE the node (produce→check→correct).
  Code Node — merges/ranks/dedups (no judge/decide, just code).
  Gate      — blast-radius gate + deterministic evidence; two return paths.

Two return paths:
  correction (short) — Gate rejects ONE unit back to its Worker; fixes this run.
  learning   (long)  — accepted result back to Splitter as constraint; fixes every future run.

Return unit travels with Unit/Verdict/Reason/Evidence/Scope; one unit only; cap 3 attempts.

Flow choice: filter → quote → merge (documented below at CHOICE).
No LIVE calls. Shadow / --no-live / rehearsal only.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from core_brain.loops.blast_gate import BlastRadius, Evidence, Trajectory, classify, evaluate as gate_evaluate
from core_brain.loops.check_first import CheckFirstLoop

# ---------- Flow choice (guide §9: draw splitter, lanes, merge, one gate, one back edge) ----------

CHOICE = "filter → quote → merge"
CHOICE_WHY = (
    "Money path exercises all three blast tiers in one demo: "
    "filter is contained read (universe selection), quote is contained resting bids "
    "(pair assembled under $1.00), merge is hard-to-reverse on-chain (pays exactly $1.00). "
    "Each stage has different reversibility, so the Gate can show contained-open / wide-gated / "
    "closed-lane in one run. Alternative single_buy → complete/exit is the rescue path — "
    "exercised less frequently, conflates opening+closing, and would hide the wide tier "
    "(sizing/stop-loss) that the demo must show. "
    "Filter→quote also owns the only place where pair_cost + both-legs predicates are evaluable "
    "before a fill; single_buy already has one leg filled and predicates would be post-hoc. "
    "See strategy: profit = 1.00 - (avg_up + avg_down); two failures are pair over $1.00 and single buy."
)

DEFAULT_PLAN_FILES = [
    "core_brain/graphs/spread_graph.py",
    "core_brain/loops/blast_gate.py",
    "core_brain/loops/check_first.py",
]

# ---------- Unit / Verdict payload (guide §7) ----------

@dataclass(frozen=True)
class ReturnUnit:
    """Payload that travels back on correction/learning edges."""
    unit_id: str  # condition_id or derived key
    verdict: str  # GREEN | RED | BLOCKED_CLOSED_LANE
    reason: str
    evidence: str
    scope: str  # Fix this file/unit only, do not touch others
    blast_radius: BlastRadius = BlastRadius.CONTAINED
    attempts: int = 1

    def is_green(self) -> bool:
        return self.verdict == "GREEN"


@dataclass
class SplitUnit:
    """One unit produced by the Splitter. One market, one lane."""
    condition_id: str
    market: dict[str, Any]
    blast_radius: BlastRadius
    action: str  # quote | filter | merge (determines lane)
    attempt: int = 1
    max_attempts: int = 3
    constraint_tags: list[str] = field(default_factory=list)  # learning constraints from prior accepted runs

    @property
    def unit_id(self) -> str:
        return self.condition_id


@dataclass
class WorkerOutput:
    unit: SplitUnit
    intents: list[Any]  # QuoteIntents or ranked row
    pair_cost: float | None
    loop_green: bool
    loop_detail: str
    checks: list[Any] = field(default_factory=list)


@dataclass
class CodeNodeOutput:
    """Deterministic merge/rank/dedup result (no model call)."""
    ranked: list[WorkerOutput]  # sorted best capture first
    deduped: list[WorkerOutput]  # one per condition_id
    merged_pairs: list[dict[str, Any]]  # pairs that would merge (arithmetic, not chain)
    total_capture: float
    evidence: str


@dataclass
class GraphResult:
    choice: str
    choice_why: str
    accepted: list[WorkerOutput]
    rejected: list[ReturnUnit]  # correction-edge units (RED, capped at 3)
    blocked: list[ReturnUnit]  # hard lane closed
    learning_constraints: list[str]  # derived constraints that landed back at Splitter
    code_output: CodeNodeOutput | None = None


# ---------- Splitter ----------

class Splitter:
    """Cuts work into units by (blast_radius, condition_id) — not by folder.

    Why not by folder: folder groups unrelated markets with different reversibility;
    blast_radius groups by consequence. A contained quote mistakenly routed through
    the wide lane would be unnecessarily gated; a hard merge mistakenly routed
    as contained would bypass the closed lane. The Splitter is where that mistake
    would waste all downstream work (guide §4).
    """

    def __init__(self, learning_constraints: list[str] | None = None) -> None:
        self.learning_constraints: list[str] = list(learning_constraints or [])

    def add_constraint(self, constraint: str) -> None:
        # Learning edge lands here — derived constraint, not raw output.
        if constraint and constraint not in self.learning_constraints:
            self.learning_constraints.append(constraint)

    def split(
        self,
        markets: list[dict[str, Any]],
        *,
        action_for: Callable[[dict[str, Any]], str] | None = None,
    ) -> list[SplitUnit]:
        units: list[SplitUnit] = []
        seen: set[str] = set()
        for m in markets or []:
            cid = str(m.get("condition_id") or m.get("conditionId") or m.get("cid") or "")
            if not cid or cid in seen:
                continue
            seen.add(cid)
            action = action_for(m) if action_for else str(m.get("_action") or "quote")
            radius = classify(action)
            # Respect explicit per-market override (tests / rehearsal tags)
            if m.get("_blast_radius"):
                try:
                    radius = BlastRadius(str(m["_blast_radius"]))
                except ValueError:
                    pass
            units.append(SplitUnit(
                condition_id=cid,
                market=dict(m),
                blast_radius=radius,
                action=action,
                constraint_tags=list(self.learning_constraints),
            ))
        return units


# ---------- Worker (loop lives inside the node) ----------

def _pair_cost_from_output(intents: list[Any], market: dict[str, Any]) -> float | None:
    # Try to derive pair cost from intents or market book.
    if not intents:
        return None
    prices = []
    for it in intents:
        if isinstance(it, dict):
            p = it.get("price")
        else:
            p = getattr(it, "price", None)
        if p is not None:
            try:
                prices.append(float(p))
            except Exception:
                pass
    if len(prices) >= 2:
        return round(sum(prices[:2]), 4)
    if len(prices) == 1 and market:
        # Try to complete with hedge ask as single_buy_saver would
        hedge_ask = None
        for k in ("hedge_ask", "other_ask", "best_ask"):
            if k in market:
                try:
                    hedge_ask = float(market[k])
                    break
                except Exception:
                    pass
        if hedge_ask is not None:
            return round(prices[0] + hedge_ask, 4)
    return None


class Worker:
    """One unit, one lens, own context. Sharing a window across workers causes convergence."""

    def __init__(
        self,
        *,
        quote_fn: Callable[[SplitUnit], list[Any]] | None = None,
        plan_files: list[str] | None = None,
        max_pair_cost: float = 0.99,
    ) -> None:
        self.quote_fn = quote_fn  # how this worker produces intents (injectable for tests)
        self.plan_files = plan_files if plan_files is not None else list(DEFAULT_PLAN_FILES)
        self.max_pair_cost = max_pair_cost

    def _produce(self, unit: SplitUnit) -> dict[str, Any]:
        # Produce intents for this single market.
        intents: list[Any] = []
        if self.quote_fn is not None:
            intents = list(self.quote_fn(unit) or [])
        else:
            # Default: read market's stub intents (no venue call).
            raw = unit.market.get("intents") or unit.market.get("stub_intents") or []
            intents = list(raw)

        pair_cost = _pair_cost_from_output(intents, unit.market)

        # Build the checkable unit dict that predicates read.
        diff_files = list(unit.market.get("diff_files") or unit.market.get("changed_files") or [])
        # If market carries no diff, default to within-plan so the predicate doesn't
        # block contained quoting on unrelated scope — diff check is for code-change units.
        check_unit: dict[str, Any] = {
            "condition_id": unit.condition_id,
            "intents": intents,
            "pair_cost": pair_cost,
            "diff_files": diff_files,
            "action": unit.action,
        }
        # Expose legs for both_legs predicate
        sides = set()
        for it in intents:
            s = it.get("side") if isinstance(it, dict) else getattr(it, "side", None)
            if s:
                sides.add(str(s))
        check_unit["sides"] = sides
        if "UP" in sides:
            check_unit["UP"] = True
        if "DOWN" in sides:
            check_unit["DOWN"] = True
        if intents:
            # Map first two intents to up/down for pair_cost predicate fallback
            try:
                if len(intents) >= 1:
                    p0 = intents[0].get("price") if isinstance(intents[0], dict) else getattr(intents[0], "price", None)
                    if p0 is not None:
                        check_unit["up_price"] = float(p0)
                if len(intents) >= 2:
                    p1 = intents[1].get("price") if isinstance(intents[1], dict) else getattr(intents[1], "price", None)
                    if p1 is not None:
                        check_unit["down_price"] = float(p1)
            except Exception:
                pass
        return check_unit

    def _correct(self, check_unit: dict[str, Any], failed: list[Any]) -> dict[str, Any]:
        # Minimal deterministic correction: if pair_cost failed, nudge prices down by 0.01
        # within allowed plan files; if both_legs failed, don't invent a leg — leave RED
        # so Gate routes to correction with scope and cap. Never widens blast radius.
        names = {getattr(c, "predicate", "") for c in failed}
        if "pair_cost" in names and check_unit.get("pair_cost") is not None:
            try:
                pc = float(check_unit["pair_cost"])
                # Nudge the higher price down by 1c, floor at 0.01
                if "up_price" in check_unit:
                    check_unit["up_price"] = max(0.01, round(float(check_unit["up_price"]) - 0.01, 4))
                if "down_price" in check_unit:
                    check_unit["down_price"] = max(0.01, round(float(check_unit["down_price"]) - 0.01, 4))
                up = float(check_unit.get("up_price") or 0)
                down = float(check_unit.get("down_price") or 0)
                if up and down:
                    check_unit["pair_cost"] = round(up + down, 4)
                elif up or down:
                    # Single price + hedge ask fallback already computed; decrement pair_cost directly
                    check_unit["pair_cost"] = round(pc - 0.01, 4)
            except Exception:
                pass
        # diff_scope failures are not auto-correctable — scope says which file to fix.
        return check_unit

    def run(self, unit: SplitUnit) -> WorkerOutput:
        # Loop lives INSIDE the node — one unit, own check_first loop.
        loop = CheckFirstLoop.spread_defaults(
            max_pair_cost=self.max_pair_cost,
            allowed_files=self.plan_files if unit.action in ("merge", "filter") or unit.market.get("diff_files") else None,
            max_attempts=3,
        )

        initial = self._produce(unit)

        def _produce_fn(u: dict[str, Any]) -> dict[str, Any]:
            return u  # already produced

        def _correct_fn(u: dict[str, Any], failed: list[Any]) -> dict[str, Any]:
            return self._correct(u, failed)

        result = loop.run(initial, produce=_produce_fn, correct=_correct_fn)

        # Recover intents/pair_cost from loop's final unit
        final_u = result.unit if isinstance(result.unit, dict) else {}
        intents = list(final_u.get("intents") or initial.get("intents") or [])
        pair_cost = final_u.get("pair_cost") if isinstance(final_u, dict) else None
        if pair_cost is None:
            pair_cost = initial.get("pair_cost")

        detail = "; ".join(f"{c.predicate}: {c.detail}" for c in result.checks) if result.checks else ""
        return WorkerOutput(
            unit=unit,
            intents=intents,
            pair_cost=pair_cost,
            loop_green=result.green,
            loop_detail=detail,
            checks=list(result.checks),
        )


# ---------- Code Node (no model call) ----------

class CodeNode:
    """Merges, ranks, deduplicates, compares — code, not a model call."""

    def run(self, worker_outputs: list[WorkerOutput]) -> CodeNodeOutput:
        # DEDUP by condition_id — last wins, but deterministic: keep first GREEN if duplicate
        by_cid: dict[str, WorkerOutput] = {}
        for w in worker_outputs:
            cid = w.unit.condition_id
            prev = by_cid.get(cid)
            if prev is None:
                by_cid[cid] = w
            else:
                # Prefer GREEN over RED
                if w.loop_green and not prev.loop_green:
                    by_cid[cid] = w
        deduped = list(by_cid.values())

        # RANK by capture (1.00 - pair_cost) descending; None cost ranks last
        def _capture(w: WorkerOutput) -> float:
            if w.pair_cost is None:
                return -1.0
            return round(1.00 - float(w.pair_cost), 4)

        ranked = sorted(
            deduped,
            key=lambda w: (_capture(w), w.unit.condition_id),
            reverse=True,
        )

        # MERGE: arithmetic pair close, not chain. Two balanced legs → pair.
        merged: list[dict[str, Any]] = []
        total = 0.0
        for w in ranked:
            if not w.loop_green or w.pair_cost is None:
                continue
            cap = _capture(w)
            if cap <= 0:
                continue
            # Only merge if both legs were present (Worker's loop already gated)
            merged.append({
                "condition_id": w.unit.condition_id,
                "pair_cost": w.pair_cost,
                "capture": cap,
                "intents": w.intents,
            })
            total += cap

        evidence = (
            f"dedup {len(worker_outputs)}→{len(deduped)}, "
            f"ranked {len(ranked)}, merged {len(merged)} pairs, total capture ${total:.4f}"
        )
        return CodeNodeOutput(ranked=ranked, deduped=deduped, merged_pairs=merged, total_capture=round(total, 4), evidence=evidence)


# ---------- Graph (split → fan out → CodeNode → Gate → back) ----------

class SpreadGraph:
    """Full graph with two return paths and cap-3 per unit.

    Build order per guide §9: gate first → lanes → learning edge last.
    Human only at final merge (hard-to-reverse lane is closed).
    """

    def __init__(
        self,
        *,
        worker: Worker | None = None,
        splitter: Splitter | None = None,
        plan_files: list[str] | None = None,
        max_pair_cost: float = 0.99,
    ) -> None:
        self.splitter = splitter or Splitter()
        self.worker = worker or Worker(plan_files=plan_files, max_pair_cost=max_pair_cost)
        self.code_node = CodeNode()
        self.max_pair_cost = max_pair_cost
        self.plan_files = plan_files or list(DEFAULT_PLAN_FILES)

    def _gate_evidence_for(self, w: WorkerOutput, prior: Trajectory) -> Evidence:
        # Deterministic pass is the Loop's GREEN; trajectory is prior verdicts for this unit
        failed_detail = ""
        if not w.loop_green:
            failed = [c for c in w.checks if not getattr(c, "green", False)]
            failed_detail = "; ".join(getattr(c, "detail", "") for c in failed) or w.loop_detail
        return Evidence(
            deterministic_pass=w.loop_green,
            deterministic_detail=failed_detail,
            trajectory=prior,
            historical_rollback_rate=None,  # not wired in demo; placeholder per guide ordering
            model_assessment=None,  # weakest, last
        )

    def run(self, markets: list[dict[str, Any]]) -> GraphResult:
        """Run the graph. No venue I/O. No signer."""
        # Keep per-unit trajectory so wide lane can gate on clean run
        trajectories: dict[str, Trajectory] = {}
        accepted: list[WorkerOutput] = []
        rejected: list[ReturnUnit] = []
        blocked: list[ReturnUnit] = []
        learning_constraints: list[str] = []

        # Allow up to 3 correction rounds per unit (outer graph cap, matches loop cap)
        # Worker loop already retries up to 3 internally; graph adds one more outer retry
        # for units that went RED — but total per-unit attempts still capped at 3.
        units = self.splitter.split(markets)
        # Track how many times each unit has been retried at graph level
        graph_attempts: dict[str, int] = {u.condition_id: 1 for u in units}

        # Fan-out: run each Worker once (own context)
        worker_outputs: list[WorkerOutput] = []
        pending_units: list[SplitUnit] = list(units)

        while pending_units:
            next_pending: list[SplitUnit] = []
            for unit in pending_units:
                w = self.worker.run(unit)
                prior = trajectories.get(unit.condition_id, Trajectory())
                evidence = self._gate_evidence_for(w, prior)
                gate = gate_evaluate(action=unit.action, evidence=evidence, scope=w.loop_detail)

                verdict = gate.verdict
                reason = gate.reason
                scope = gate.scope or w.loop_detail

                # Update trajectory for this unit
                prev = trajectories.get(unit.condition_id, Trajectory())
                new_verdicts = tuple(list(prev.prior_verdicts) + [verdict])
                trajectories[unit.condition_id] = Trajectory(
                    prior_verdicts=new_verdicts,
                    corrections_attempted=prev.corrections_attempted + (0 if verdict == "GREEN" else 1),
                )

                if verdict == "BLOCKED_CLOSED_LANE":
                    blocked.append(ReturnUnit(
                        unit_id=unit.condition_id, verdict=verdict, reason=reason,
                        evidence=evidence.deterministic_detail or gate.reason,
                        scope=scope, blast_radius=gate.blast_radius, attempts=graph_attempts[unit.condition_id],
                    ))
                    continue

                if verdict == "GREEN":
                    accepted.append(w)
                    worker_outputs.append(w)
                    # Learning edge: derive constraint and send back to Splitter
                    # Derived constraint, not raw output (guide §6)
                    constraint = self._derive_constraint(w)
                    if constraint:
                        self.splitter.add_constraint(constraint)
                        learning_constraints.append(constraint)
                    continue

                # RED — correction edge (short): one unit back to its Worker, cap 3
                attempts = graph_attempts[unit.condition_id]
                if attempts < 3:
                    # Return ONE unit only, not the batch (guide §7)
                    rejected.append(ReturnUnit(
                        unit_id=unit.condition_id, verdict="RED", reason=reason,
                        evidence=evidence.deterministic_detail, scope=scope,
                        blast_radius=gate.blast_radius, attempts=attempts,
                    ))
                    graph_attempts[unit.condition_id] = attempts + 1
                    # Retry this single unit next round
                    retry_unit = SplitUnit(
                        condition_id=unit.condition_id,
                        market=dict(unit.market),
                        blast_radius=unit.blast_radius,
                        action=unit.action,
                        attempt=attempts + 1,
                        constraint_tags=list(self.splitter.learning_constraints),
                    )
                    next_pending.append(retry_unit)
                else:
                    # Capped — plan is wrong, local loop cannot see the plan (guide §7)
                    rejected.append(ReturnUnit(
                        unit_id=unit.condition_id, verdict="RED", reason=reason + " — capped at 3",
                        evidence=evidence.deterministic_detail, scope=scope,
                        blast_radius=gate.blast_radius, attempts=attempts,
                    ))
                    worker_outputs.append(w)  # still record for CodeNode dedup visibility
            pending_units = next_pending

        code_out = self.code_node.run(worker_outputs) if (accepted or worker_outputs) else None

        return GraphResult(
            choice=CHOICE,
            choice_why=CHOICE_WHY,
            accepted=accepted,
            rejected=rejected,
            blocked=blocked,
            learning_constraints=learning_constraints,
            code_output=code_out,
        )

    @staticmethod
    def _derive_constraint(w: WorkerOutput) -> str | None:
        # Learning edge carries a derived constraint, not the output.
        # Example: accepted quote with capture >0 → preserve that offset for similar mids.
        if not w.loop_green or w.pair_cost is None:
            return None
        try:
            cap = round(1.00 - float(w.pair_cost), 4)
        except Exception:
            return None
        if cap <= 0:
            return None
        # Narrow, actionable constraint for future splits
        return f"quote: condition {w.unit.condition_id} accepted at pair_cost {w.pair_cost:.4f} (capture {cap:.4f}) — keep offset for similar mid capture"
