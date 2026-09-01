"""check_first loop — produce → check → correct → repeat until GREEN.

Guide §1: write the condition first, as a program-evaluable predicate.
  ✅ Green: pair_cost <= 0.99
  ✅ Green: both legs present (UP + DOWN)
  ✅ Green: diff touches only files listed in plan
  ❌ Not a check: looks good / confident / no error / no exception

Loop lives inside a node (guide §5). One unit, own context, cap 3.
No venue calls, no signer, no LIVE path.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Sequence


# ---------- GREEN predicates (program-evaluable, not model-evaluated) ----------

@dataclass(frozen=True)
class PredicateResult:
    passed: bool
    name: str
    detail: str  # Evidence: why it passed/failed, with values
    scope: str = ""  # Which file/unit to fix if failed


Predicate = Callable[[Any], PredicateResult]


def pair_cost_predicate(max_pair_cost: float = 0.99) -> Predicate:
    """GREEN only if pair_cost <= cap. Cost is avg_up + avg_down.

    Unit is expected to carry pair_cost or avg_up/avg_down, or a dict with them.
    For QuoteIntent pairs, caller should compute cost before checking.
    """
    def _pred(unit: Any) -> PredicateResult:
        # Support dict, dataclass, or tuple of (up_price, down_price)
        cost: float | None = None
        if isinstance(unit, dict):
            if "pair_cost" in unit:
                cost = float(unit["pair_cost"])
            elif "avg_up" in unit and "avg_down" in unit:
                cost = float(unit["avg_up"]) + float(unit["avg_down"])
            elif "up_price" in unit and "down_price" in unit:
                cost = float(unit["up_price"]) + float(unit["down_price"])
        elif isinstance(unit, (list, tuple)) and len(unit) == 2:
            try:
                cost = float(unit[0]) + float(unit[1])
            except Exception:
                cost = None
        else:
            # Try attribute access
            for attr in ("pair_cost", "pairCost"):
                if hasattr(unit, attr):
                    try:
                        cost = float(getattr(unit, attr))
                        break
                    except Exception:
                        pass
            if cost is None and hasattr(unit, "avg"):
                # Inventory-like? pair_cost() method
                try:
                    pc = unit.pair_cost() if callable(getattr(unit, "pair_cost", None)) else None
                    if pc and pc > 0:
                        cost = float(pc)
                except Exception:
                    pass
        if cost is None:
            return PredicateResult(False, "pair_cost", "no pair_cost on unit — cannot prove GREEN", scope="unit")
        if cost <= max_pair_cost + 1e-9:
            return PredicateResult(True, "pair_cost", f"pair_cost {cost:.4f} <= {max_pair_cost:.3f} GREEN")
        return PredicateResult(
            False, "pair_cost",
            f"pair_cost {cost:.4f} > {max_pair_cost:.3f} — booked loss on $1.00 payout",
            scope="pair price",
        )
    _pred.__name__ = f"pair_cost<={max_pair_cost}"
    return _pred


def both_legs_predicate() -> Predicate:
    """GREEN only if both legs present (UP + DOWN). One leg = directional bet."""
    def _pred(unit: Any) -> PredicateResult:
        has_up = has_down = False
        detail = ""
        if isinstance(unit, dict):
            has_up = bool(unit.get("up_price") or unit.get("up_cost") or unit.get("UP") or unit.get("up"))
            has_down = bool(unit.get("down_price") or unit.get("down_cost") or unit.get("DOWN") or unit.get("down"))
            # Also handle intents list
            if "intents" in unit:
                sides = {getattr(i, "side", None) or (i.get("side") if isinstance(i, dict) else None) for i in unit["intents"]}
                has_up = "UP" in sides
                has_down = "DOWN" in sides
                detail = f"sides={sides}"
            elif "legs" in unit:
                legs = unit["legs"]
                if isinstance(legs, dict):
                    has_up = "UP" in legs or "up" in legs
                    has_down = "DOWN" in legs or "down" in legs
                elif isinstance(legs, (list, tuple)):
                    has_up = len(legs) >= 1 and bool(legs[0])
                    has_down = len(legs) >= 2 and bool(legs[1])
        elif isinstance(unit, (list, tuple)):
            # [up_intent, down_intent] or [up_price, down_price]
            has_up = len(unit) >= 1 and unit[0] is not None
            has_down = len(unit) >= 2 and unit[1] is not None
        else:
            # dataclass Inventory
            if hasattr(unit, "up_shares") and hasattr(unit, "down_shares"):
                has_up = float(getattr(unit, "up_shares") or 0) > 0
                has_down = float(getattr(unit, "down_shares") or 0) > 0
                detail = f"up_shares={getattr(unit,'up_shares')}, down_shares={getattr(unit,'down_shares')}"
        passed = has_up and has_down
        if passed:
            return PredicateResult(True, "both_legs", f"both legs present {detail} GREEN")
        missing = []
        if not has_up:
            missing.append("UP")
        if not has_down:
            missing.append("DOWN")
        return PredicateResult(False, "both_legs", f"single buy — missing {', '.join(missing)} leg (directional bet)", scope="missing leg")
    _pred.__name__ = "both_legs"
    return _pred


def diff_scope_predicate(allowed_files: Sequence[str]) -> Predicate:
    """GREEN only if diff touches only files listed in plan.

    Unit is expected to carry `diff_files: list[str]` or `changed_files`.
    Exact match against allowlist; no glob for demo determinism.
    """
    allowed = set(allowed_files or [])
    def _pred(unit: Any) -> PredicateResult:
        files: list[str] = []
        if isinstance(unit, dict):
            files = list(unit.get("diff_files") or unit.get("changed_files") or unit.get("files") or [])
        elif hasattr(unit, "diff_files"):
            try:
                files = list(getattr(unit, "diff_files") or [])
            except Exception:
                files = []
        if not files:
            # No diff → nothing to scope-check; treat as GREEN (e.g., pure quoting unit)
            return PredicateResult(True, "diff_scope", "no diff files — scope check GREEN (nothing to review)")
        bad = [f for f in files if f not in allowed]
        if not bad:
            return PredicateResult(True, "diff_scope", f"diff {files} within plan {sorted(allowed)} GREEN")
        return PredicateResult(
            False, "diff_scope",
            f"diff touches outside plan: {bad} not in {sorted(allowed)}",
            scope=", ".join(bad),
        )
    _pred.__name__ = f"diff_scope[{','.join(allowed)}]"
    return _pred


# ---------- Loop ----------

@dataclass
class CheckResult:
    green: bool
    predicate: str
    detail: str
    scope: str = ""


@dataclass
class LoopResult:
    green: bool
    attempts: int
    unit: Any
    checks: list[CheckResult] = field(default_factory=list)
    correction_log: list[str] = field(default_factory=list)
    capped: bool = False  # hit max_attempts without GREEN


ProduceFn = Callable[[Any], Any]
CorrectFn = Callable[[Any, list[CheckResult]], Any]


class CheckFirstLoop:
    """Generic produce → check → correct loop. Cap 3 per guide §7."""

    def __init__(
        self,
        predicates: Sequence[Predicate],
        *,
        max_attempts: int = 3,
    ) -> None:
        if max_attempts < 1 or max_attempts > 3:
            raise ValueError("max_attempts must be 1..3 per guide cap")
        self.predicates = list(predicates)
        self.max_attempts = max_attempts

    def check(self, unit: Any) -> list[CheckResult]:
        results: list[CheckResult] = []
        for pred in self.predicates:
            r = pred(unit)
            results.append(CheckResult(green=r.passed, predicate=r.name, detail=r.detail, scope=r.scope))
        return results

    def is_green(self, checks: list[CheckResult]) -> bool:
        return all(c.green for c in checks)

    def run(
        self,
        initial_unit: Any,
        *,
        produce: ProduceFn | None = None,
        correct: CorrectFn | None = None,
    ) -> LoopResult:
        """Run produce → check → correct until GREEN or cap.

        - produce(unit) → new_unit is optional; if omitted, check runs on initial_unit directly
          then correct is used to fix it.
        - correct(unit, failed_checks) → fixed_unit is required when first check is RED.
        - If check is GREEN on first attempt, no correction runs — that's the loop's proof.
        """
        unit = initial_unit
        if produce is not None:
            unit = produce(unit)

        correction_log: list[str] = []
        last_checks: list[CheckResult] = []

        for attempt in range(1, self.max_attempts + 1):
            checks = self.check(unit)
            last_checks = checks
            if self.is_green(checks):
                return LoopResult(green=True, attempts=attempt, unit=unit, checks=checks, correction_log=correction_log)

            # RED → need correction, but only if not on last attempt
            if attempt == self.max_attempts:
                break
            if correct is None:
                # No corrector — cannot proceed; loop would be a scheduler, not a loop.
                correction_log.append(f"attempt {attempt}: no corrector — stopping RED")
                break
            failed = [c for c in checks if not c.green]
            try:
                unit = correct(unit, failed)
            except Exception as e:
                correction_log.append(f"attempt {attempt}: corrector raised {e!r}")
                break
            correction_log.append(f"attempt {attempt}: corrected {', '.join(c.predicate for c in failed)} → retry")

        return LoopResult(
            green=False,
            attempts=self.max_attempts,
            unit=unit,
            checks=last_checks,
            correction_log=correction_log,
            capped=True,
        )

    # Convenience: build a loop with spread-hunter defaults
    @classmethod
    def spread_defaults(
        cls,
        *,
        max_pair_cost: float = 0.99,
        allowed_files: Sequence[str] | None = None,
        max_attempts: int = 3,
    ) -> "CheckFirstLoop":
        preds: list[Predicate] = [
            pair_cost_predicate(max_pair_cost),
            both_legs_predicate(),
        ]
        if allowed_files is not None:
            preds.append(diff_scope_predicate(allowed_files))
        return cls(preds, max_attempts=max_attempts)
