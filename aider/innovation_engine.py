"""Coordinator for adaptive context, risk analysis, and resumable state."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from aider.innovation_anchors import SymbolAnchor, SymbolAnchorIndex
from aider.innovation_budgets import AdaptiveBudgetPlanner
from aider.innovation_capsules import (
    ContextCapsule,
    ContextLeaseBook,
    ContextProvenanceLedger,
)
from aider.innovation_context import FloatingContextManager, FloatingContextState
from aider.innovation_risk import BlastRadiusAnalyzer, BlastRadiusReport


@dataclass(frozen=True)
class InnovationReport:
    query: str
    context_slice_ids: tuple[str, ...]
    risk: BlastRadiusReport
    budgets: dict[str, dict[str, float | int]]
    symbol_anchors: dict[str, tuple[SymbolAnchor, ...]]


class InnovationEngine:
    def __init__(
        self,
        workspace: str | Path,
        *,
        max_tokens: int = 4096,
        reserve_tokens: int = 768,
        lease_turns: int = 3,
    ) -> None:
        self.workspace = Path(workspace).resolve()
        self.state = FloatingContextState()
        self.context = FloatingContextManager(
            max_tokens=max_tokens,
            reserve_tokens=reserve_tokens,
            state=self.state,
        )
        self.leases = ContextLeaseBook(default_turns=lease_turns)
        self.provenance = ContextProvenanceLedger()
        self.anchors = SymbolAnchorIndex()
        self.risk_analyzer = BlastRadiusAnalyzer()
        self.budget_planner = AdaptiveBudgetPlanner()
        self.last_packet = None

    def analyze(
        self,
        files: Mapping[str, str],
        query: str,
        *,
        diff: str = "",
        changed_paths: tuple[str, ...] = (),
        explicit_paths: tuple[str, ...] = (),
        total_seconds: float = 120.0,
    ) -> InnovationReport:
        packet = self.context.build_packet(
            files,
            query,
            changed_paths=changed_paths,
            explicit_paths=explicit_paths,
        )
        self.last_packet = packet
        identifiers = self.leases.reconcile(packet)
        self.provenance.record_packet(packet)
        risk = self.risk_analyzer.analyze(diff)
        budgets = self.budget_planner.plan(
            total_tokens=self.context.max_tokens,
            total_seconds=total_seconds,
            risk_score=risk.score,
            has_tests=any("test" in path.lower() for path in files),
        )
        anchors = {
            path: tuple(self.anchors.index(text, path=path))
            for path, text in files.items()
        }
        return InnovationReport(
            query,
            identifiers,
            risk,
            budgets.report(),
            anchors,
        )

    def mark_slice_used(self, identifier: str, *, query: str = "") -> None:
        self.leases.mark_used(identifier)
        self.provenance.record_use(identifier, query=query)

    def advance_turn(self) -> tuple[str, ...]:
        return self.leases.advance()

    def checkpoint(self, path: str | Path) -> ContextCapsule:
        if self.last_packet is None:
            raise RuntimeError("analyze must run before checkpoint")
        capsule = ContextCapsule.capture(
            self.last_packet,
            self.state,
            self.leases,
            self.provenance,
        )
        capsule.save(path)
        return capsule
