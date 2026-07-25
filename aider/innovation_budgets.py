"""Stage-specific token and wall-clock budgets for bounded coding workflows."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Mapping


class WorkStage(str, Enum):
    SEARCH = "search"
    EDIT = "edit"
    TEST = "test"
    REPAIR = "repair"


class BudgetExceeded(RuntimeError):
    pass


@dataclass
class StageBudget:
    token_limit: int
    seconds_limit: float
    used_tokens: int = 0
    used_seconds: float = 0.0

    @property
    def remaining_tokens(self) -> int:
        return max(0, self.token_limit - self.used_tokens)

    @property
    def remaining_seconds(self) -> float:
        return max(0.0, self.seconds_limit - self.used_seconds)

    def can_consume(self, *, tokens: int = 0, seconds: float = 0.0) -> bool:
        return tokens <= self.remaining_tokens and seconds <= self.remaining_seconds


class BudgetLedger:
    def __init__(self, budgets: Mapping[WorkStage, StageBudget]) -> None:
        self.budgets = {stage: StageBudget(**asdict(budget)) for stage, budget in budgets.items()}
        missing = set(WorkStage) - set(self.budgets)
        if missing:
            names = ", ".join(sorted(stage.value for stage in missing))
            raise ValueError(f"missing stage budgets: {names}")

    def consume(self, stage: WorkStage, *, tokens: int = 0, seconds: float = 0.0) -> None:
        if tokens < 0 or seconds < 0:
            raise ValueError("budget consumption cannot be negative")
        budget = self.budgets[stage]
        if not budget.can_consume(tokens=tokens, seconds=seconds):
            raise BudgetExceeded(
                f"{stage.value} budget exceeded: requested {tokens} tokens/{seconds:.2f}s, "
                f"remaining {budget.remaining_tokens} tokens/{budget.remaining_seconds:.2f}s"
            )
        budget.used_tokens += tokens
        budget.used_seconds += seconds

    def borrow(
        self,
        source: WorkStage,
        target: WorkStage,
        *,
        tokens: int = 0,
        seconds: float = 0.0,
    ) -> None:
        if source == target:
            return
        if tokens < 0 or seconds < 0:
            raise ValueError("borrowed budget cannot be negative")
        source_budget = self.budgets[source]
        if tokens > source_budget.remaining_tokens or seconds > source_budget.remaining_seconds:
            raise BudgetExceeded(f"cannot borrow beyond unused {source.value} budget")
        source_budget.token_limit -= tokens
        source_budget.seconds_limit -= seconds
        self.budgets[target].token_limit += tokens
        self.budgets[target].seconds_limit += seconds

    def report(self) -> dict[str, dict[str, float | int]]:
        return {
            stage.value: {
                "token_limit": budget.token_limit,
                "used_tokens": budget.used_tokens,
                "remaining_tokens": budget.remaining_tokens,
                "seconds_limit": round(budget.seconds_limit, 3),
                "used_seconds": round(budget.used_seconds, 3),
                "remaining_seconds": round(budget.remaining_seconds, 3),
            }
            for stage, budget in self.budgets.items()
        }


class AdaptiveBudgetPlanner:
    """Allocate bounded resources while reserving more verification for risky edits."""

    def plan(
        self,
        *,
        total_tokens: int,
        total_seconds: float,
        risk_score: int = 0,
        has_tests: bool = True,
    ) -> BudgetLedger:
        if total_tokens < 400:
            raise ValueError("total_tokens must be at least 400")
        if total_seconds <= 0:
            raise ValueError("total_seconds must be positive")
        risk = min(100, max(0, risk_score)) / 100.0
        fractions = {
            WorkStage.SEARCH: 0.25 - 0.07 * risk,
            WorkStage.EDIT: 0.30 - 0.05 * risk,
            WorkStage.TEST: 0.25 + 0.07 * risk,
            WorkStage.REPAIR: 0.20 + 0.05 * risk,
        }
        if not has_tests:
            moved = fractions[WorkStage.TEST] * 0.55
            fractions[WorkStage.TEST] -= moved
            fractions[WorkStage.REPAIR] += moved * 0.65
            fractions[WorkStage.EDIT] += moved * 0.35
        token_allocations = self._integer_allocations(total_tokens, fractions)
        budgets = {
            stage: StageBudget(
                token_limit=token_allocations[stage],
                seconds_limit=total_seconds * fractions[stage],
            )
            for stage in WorkStage
        }
        return BudgetLedger(budgets)

    @staticmethod
    def _integer_allocations(
        total: int, fractions: Mapping[WorkStage, float]
    ) -> dict[WorkStage, int]:
        raw = {stage: total * fraction for stage, fraction in fractions.items()}
        output = {stage: int(value) for stage, value in raw.items()}
        remainder = total - sum(output.values())
        order = sorted(raw, key=lambda stage: raw[stage] - output[stage], reverse=True)
        for stage in order[:remainder]:
            output[stage] += 1
        return output
