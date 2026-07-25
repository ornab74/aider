"""Compressed repair trajectory memory for loop detection and prior-solution recall."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable

from aider.innovation_context import query_terms


@dataclass(frozen=True)
class TrajectoryEvent:
    phase: str
    action: str
    outcome: str
    tokens: int = 0
    failure_fingerprint: str = ""


@dataclass(frozen=True)
class TrajectorySummary:
    task: str
    success: bool
    phases: tuple[str, ...]
    actions: tuple[str, ...]
    failure_fingerprints: tuple[str, ...]
    total_tokens: int
    loop_detected: bool
    fingerprint: str


@dataclass(frozen=True)
class TrajectoryMatch:
    summary: TrajectorySummary
    score: float
    reasons: tuple[str, ...]


@dataclass
class RepairTrajectoryMemory:
    summaries: list[TrajectorySummary] = field(default_factory=list)

    def summarize(
        self,
        task: str,
        events: Iterable[TrajectoryEvent],
        *,
        success: bool,
    ) -> TrajectorySummary:
        records = tuple(events)
        phases = self._dedupe(item.phase for item in records)
        actions = self._dedupe(item.action for item in records)
        failures = tuple(
            item.failure_fingerprint for item in records if item.failure_fingerprint
        )
        loop = len(failures) != len(set(failures))
        payload = {
            "task": task,
            "success": success,
            "phases": phases,
            "actions": actions,
            "failures": failures,
        }
        fingerprint = hashlib.sha256(
            json.dumps(payload, sort_keys=True).encode()
        ).hexdigest()[:24]
        summary = TrajectorySummary(
            task,
            success,
            phases,
            actions,
            failures,
            sum(max(0, item.tokens) for item in records),
            loop,
            fingerprint,
        )
        self.summaries.append(summary)
        return summary

    def recall(self, task: str, *, limit: int = 5) -> tuple[TrajectoryMatch, ...]:
        requested = query_terms(task)
        matches = []
        for summary in self.summaries:
            overlap = requested & query_terms(summary.task + " " + " ".join(summary.actions))
            score = len(overlap) * 8.0 + (12.0 if summary.success else 0.0)
            if summary.loop_detected:
                score -= 4.0
            if score:
                reasons = []
                if overlap:
                    reasons.append("task/action overlap")
                if summary.success:
                    reasons.append("successful prior trajectory")
                if summary.loop_detected:
                    reasons.append("contained a repair loop")
                matches.append(TrajectoryMatch(summary, score, tuple(reasons)))
        matches.sort(key=lambda item: (-item.score, item.summary.fingerprint))
        return tuple(matches[:limit])

    def save(self, path: str | Path) -> None:
        Path(path).write_text(
            json.dumps([asdict(item) for item in self.summaries], indent=2),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: str | Path) -> "RepairTrajectoryMemory":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        summaries = []
        for item in payload:
            normalized = dict(item)
            normalized["phases"] = tuple(normalized.get("phases", ()))
            normalized["actions"] = tuple(normalized.get("actions", ()))
            normalized["failure_fingerprints"] = tuple(
                normalized.get("failure_fingerprints", ())
            )
            summaries.append(TrajectorySummary(**normalized))
        return cls(summaries)

    @staticmethod
    def _dedupe(values: Iterable[str]) -> tuple[str, ...]:
        output = []
        for value in values:
            if value and (not output or output[-1] != value):
                output.append(value)
        return tuple(output)
