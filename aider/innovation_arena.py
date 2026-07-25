"""Deterministic comparison of speculative patches before any write is applied."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Mapping, Sequence

from aider.innovation_contracts import (
    ContractSet,
    ContractValidation,
    InvariantContractValidator,
)
from aider.innovation_risk import BlastRadiusAnalyzer, BlastRadiusReport


@dataclass(frozen=True)
class PatchCandidate:
    name: str
    files: Mapping[str, str]
    diff: str
    tests_selected: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class PatchEvaluation:
    candidate: PatchCandidate
    score: float
    contracts: ContractValidation
    risk: BlastRadiusReport
    syntax_failures: tuple[str, ...]
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class PatchArenaReport:
    evaluations: tuple[PatchEvaluation, ...]
    winner: PatchEvaluation | None


class SpeculativePatchArena:
    def __init__(
        self,
        validator: InvariantContractValidator | None = None,
        risk_analyzer: BlastRadiusAnalyzer | None = None,
    ) -> None:
        self.validator = validator or InvariantContractValidator()
        self.risk_analyzer = risk_analyzer or BlastRadiusAnalyzer()

    def evaluate(
        self,
        candidates: Sequence[PatchCandidate],
        contracts: Sequence[ContractSet],
    ) -> PatchArenaReport:
        evaluations = [self._evaluate_one(item, contracts) for item in candidates]
        evaluations.sort(key=lambda item: (-item.score, item.candidate.name))
        viable = [
            item
            for item in evaluations
            if item.contracts.valid and not item.syntax_failures
        ]
        return PatchArenaReport(tuple(evaluations), viable[0] if viable else None)

    def _evaluate_one(
        self,
        candidate: PatchCandidate,
        contracts: Sequence[ContractSet],
    ) -> PatchEvaluation:
        contract_result = self.validator.validate(contracts, candidate.files)
        risk = self.risk_analyzer.analyze(candidate.diff)
        syntax_failures = tuple(self._syntax_failures(candidate.files))
        changed_lines = sum(
            1
            for line in candidate.diff.splitlines()
            if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
        )
        score = 100.0
        score -= contract_result.blocking_count * 35.0
        score -= len(syntax_failures) * 45.0
        score -= risk.score * 0.28
        score -= min(18.0, changed_lines * 0.08)
        score += min(12.0, len(candidate.tests_selected) * 2.0)
        reasons = [
            f"risk={risk.score}",
            f"changed_lines={changed_lines}",
            f"tests={len(candidate.tests_selected)}",
        ]
        if contract_result.blocking_count:
            reasons.append(f"blocking_contracts={contract_result.blocking_count}")
        if syntax_failures:
            reasons.append(f"syntax_failures={len(syntax_failures)}")
        return PatchEvaluation(
            candidate,
            round(score, 3),
            contract_result,
            risk,
            syntax_failures,
            tuple(reasons),
        )

    @staticmethod
    def _syntax_failures(files: Mapping[str, str]) -> list[str]:
        failures = []
        for path, text in files.items():
            if not path.endswith(".py"):
                continue
            try:
                ast.parse(text)
            except SyntaxError as exc:
                failures.append(f"{path}:{exc.lineno}:{exc.msg}")
        return failures
