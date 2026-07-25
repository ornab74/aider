"""Context-pressure telemetry and bounded mitigation recommendations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from aider.innovation_context import ContextPacket, query_terms


@dataclass(frozen=True)
class ContextPressureReport:
    saturation: float
    redundancy: float
    churn: float
    cache_hit: bool
    pressure_score: int
    band: str
    recommendations: tuple[str, ...]


class ContextPressureMonitor:
    def assess(
        self,
        packet: ContextPacket,
        *,
        previous_slice_ids: Iterable[str] = (),
        current_slice_ids: Iterable[str] = (),
        cache_hit: bool = False,
    ) -> ContextPressureReport:
        saturation = min(1.0, packet.token_estimate / max(1, packet.budget))
        redundancy = self._redundancy(packet)
        previous = set(previous_slice_ids)
        current = set(current_slice_ids)
        if not previous and not current:
            churn = 0.0
        else:
            churn = 1.0 - len(previous & current) / max(1, len(previous | current))
        raw = saturation * 55 + redundancy * 25 + churn * 20
        if cache_hit:
            raw -= 5
        score = max(0, min(100, round(raw)))
        if score >= 80:
            band = "critical"
        elif score >= 60:
            band = "high"
        elif score >= 35:
            band = "medium"
        else:
            band = "low"
        recommendations = []
        if saturation >= 0.85:
            recommendations.append("expire low-value leases or lower per-file slice limits")
        if redundancy >= 0.55:
            recommendations.append("apply diversity reranking before the next model call")
        if churn >= 0.65:
            recommendations.append(
                "checkpoint stable facts and pin only the active dependency neighborhood"
            )
        if score >= 70:
            recommendations.append("route to a larger-context model or split the task into stages")
        if not recommendations:
            recommendations.append("retain the current bounded context plan")
        return ContextPressureReport(
            round(saturation, 4),
            round(redundancy, 4),
            round(churn, 4),
            cache_hit,
            score,
            band,
            tuple(recommendations),
        )

    @staticmethod
    def _redundancy(packet: ContextPacket) -> float:
        slices = packet.slices
        if len(slices) < 2:
            return 0.0
        similarities = []
        for index, left in enumerate(slices):
            left_terms = query_terms(left.text)
            for right in slices[index + 1 :]:
                right_terms = query_terms(right.text)
                union = left_terms | right_terms
                similarities.append(len(left_terms & right_terms) / len(union) if union else 0.0)
        return sum(similarities) / max(1, len(similarities))
