"""Causal failure hypotheses from traces, changes, and call-graph structure."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

from aider.innovation_context import query_terms
from aider.innovation_failures import FailureLocalizer, LocalizedFailure
from aider.innovation_graph import PythonCallGraph


@dataclass(frozen=True)
class CauseHypothesis:
    identifier: str
    path: str
    symbol: str
    score: float
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class CausalFailureReport:
    failure: LocalizedFailure
    hypotheses: tuple[CauseHypothesis, ...]
    changed_paths: tuple[str, ...]


class CausalFailureAnalyzer:
    def __init__(self, localizer: FailureLocalizer | None = None) -> None:
        self.localizer = localizer or FailureLocalizer()

    def analyze(
        self,
        output: str,
        files: Mapping[str, str],
        *,
        changed_paths: Iterable[str] = (),
        limit: int = 10,
    ) -> CausalFailureReport:
        failure = self.localizer.parse(output)
        graph = PythonCallGraph(files)
        changed = {str(Path(path)) for path in changed_paths}
        frame_paths = {str(Path(frame.path)) for frame in failure.frames}
        failure_terms = query_terms(
            " ".join((failure.test_name, failure.message, failure.assertion))
        )
        hypotheses: list[CauseHypothesis] = []
        for identifier, node in graph.nodes.items():
            score = 0.0
            reasons = []
            if node.path in changed:
                score += 28.0
                reasons.append("recently changed")
            if node.path in frame_paths or any(node.path.endswith(path) for path in frame_paths):
                score += 36.0
                reasons.append("appears in traceback")
            overlap = failure_terms & query_terms(node.qualified_name + " " + node.source)
            if overlap:
                score += min(24.0, len(overlap) * 6.0)
                reasons.append("failure-term overlap")
            callers = len(graph.reverse_edges.get(identifier, set()))
            callees = len(graph.edges.get(identifier, set()))
            if callers or callees:
                score += min(12.0, callers * 2.5 + callees * 1.5)
                reasons.append("call-graph connectivity")
            if score:
                hypotheses.append(
                    CauseHypothesis(
                        identifier,
                        node.path,
                        node.qualified_name,
                        round(score, 3),
                        tuple(reasons),
                    )
                )
        hypotheses.sort(key=lambda item: (-item.score, item.identifier))
        return CausalFailureReport(
            failure,
            tuple(hypotheses[:limit]),
            tuple(sorted(changed)),
        )
