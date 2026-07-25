"""Diversity-aware context reranking for small coding-model prompts."""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from typing import Iterable

from aider.innovation_context import ContextPacket, ContextSlice, estimate_tokens, query_terms


@dataclass(frozen=True)
class DiversityPolicy:
    relevance_weight: float = 0.68
    novelty_weight: float = 0.22
    file_spread_weight: float = 0.10
    same_file_penalty: float = 0.18
    max_slices_per_file: int = 2

    def __post_init__(self) -> None:
        if self.max_slices_per_file < 1:
            raise ValueError("max_slices_per_file must be positive")
        if min(
            self.relevance_weight,
            self.novelty_weight,
            self.file_spread_weight,
            self.same_file_penalty,
        ) < 0:
            raise ValueError("diversity policy weights cannot be negative")


@dataclass(frozen=True)
class DiversityReport:
    packet: ContextPacket
    file_entropy: float
    distinct_files: int
    covered_terms: tuple[str, ...]
    omitted_for_similarity: int


class DiversityAwareReranker:
    """Use maximal-marginal relevance to avoid repetitive context packets."""

    def __init__(self, policy: DiversityPolicy | None = None) -> None:
        self.policy = policy or DiversityPolicy()

    def rerank(
        self,
        packet: ContextPacket,
        *,
        query: str | None = None,
        budget: int | None = None,
    ) -> DiversityReport:
        target_query = query if query is not None else packet.query
        target_budget = budget if budget is not None else packet.budget
        candidates = list(packet.slices)
        if not candidates:
            return DiversityReport(packet, 0.0, 0, (), 0)

        maximum_score = max(item.score for item in candidates) or 1.0
        selected: list[ContextSlice] = []
        file_counts: Counter[str] = Counter()
        consumed = estimate_tokens(target_query) + 48
        omitted_similarity = 0

        while candidates:
            ranked = sorted(
                candidates,
                key=lambda item: (
                    -self._marginal_score(item, selected, file_counts, maximum_score),
                    item.path,
                    item.start_line,
                ),
            )
            candidate = ranked[0]
            candidates.remove(candidate)
            if file_counts[candidate.path] >= self.policy.max_slices_per_file:
                omitted_similarity += 1
                continue
            cost = candidate.token_estimate + 20
            if consumed + cost > target_budget:
                continue
            selected.append(candidate)
            file_counts[candidate.path] += 1
            consumed += cost

        covered = set()
        requested = query_terms(target_query)
        for item in selected:
            covered.update(requested & query_terms(item.text))
        output = ContextPacket(
            query=target_query,
            slices=tuple(selected),
            token_estimate=consumed,
            budget=target_budget,
            omitted_candidates=(
                packet.omitted_candidates + max(0, len(packet.slices) - len(selected))
            ),
        )
        return DiversityReport(
            output,
            self._entropy(file_counts.values()),
            len(file_counts),
            tuple(sorted(covered)),
            omitted_similarity,
        )

    def _marginal_score(
        self,
        candidate: ContextSlice,
        selected: Iterable[ContextSlice],
        file_counts: Counter[str],
        maximum_score: float,
    ) -> float:
        chosen = tuple(selected)
        relevance = candidate.score / maximum_score
        similarity = max(
            (self._similarity(candidate, existing) for existing in chosen),
            default=0.0,
        )
        novelty = 1.0 - similarity
        file_bonus = 1.0 if not file_counts[candidate.path] else 0.0
        penalty = self.policy.same_file_penalty * file_counts[candidate.path]
        return (
            self.policy.relevance_weight * relevance
            + self.policy.novelty_weight * novelty
            + self.policy.file_spread_weight * file_bonus
            - penalty
        )

    @staticmethod
    def _similarity(left: ContextSlice, right: ContextSlice) -> float:
        left_terms = query_terms(left.text)
        right_terms = query_terms(right.text)
        if not left_terms or not right_terms:
            lexical = 0.0
        else:
            lexical = len(left_terms & right_terms) / len(left_terms | right_terms)
        path_overlap = 0.25 if left.path == right.path else 0.0
        range_overlap = 0.0
        if left.path == right.path:
            overlap = max(
                0,
                min(left.end_line, right.end_line) - max(left.start_line, right.start_line) + 1,
            )
            span = max(left.end_line, right.end_line) - min(left.start_line, right.start_line) + 1
            range_overlap = overlap / max(1, span)
        return min(1.0, lexical * 0.65 + path_overlap + range_overlap * 0.35)

    @staticmethod
    def _entropy(counts: Iterable[int]) -> float:
        values = tuple(counts)
        total = sum(values)
        if total <= 0 or len(values) <= 1:
            return 0.0
        entropy = -sum((value / total) * math.log2(value / total) for value in values)
        return entropy / math.log2(len(values))
