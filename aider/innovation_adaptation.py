"""Language-aware skill hot swapping and safe draft skill authoring."""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from aider.innovation_skills import SkillMatch, SkillRegistry

_LANGUAGE_BY_SUFFIX = {
    ".py": "python", ".pyi": "python", ".dart": "dart",
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript",
    ".ts": "typescript", ".tsx": "typescript", ".html": "html",
    ".htm": "html", ".ps1": "powershell", ".psm1": "powershell",
    ".sh": "terminal", ".bash": "terminal",
}
_FENCE_RE = re.compile(r"```\s*([\w+-]+)")


@dataclass(frozen=True)
class LanguageSignal:
    language: str
    confidence: float
    evidence: tuple[str, ...]


class LanguageDetector:
    def detect(self, query: str, paths: Iterable[str] = ()) -> LanguageSignal:
        votes: Counter[str] = Counter()
        evidence = []
        for raw_path in paths:
            language = _LANGUAGE_BY_SUFFIX.get(Path(raw_path).suffix.lower())
            if language:
                votes[language] += 3
                evidence.append(f"{raw_path} -> {language}")
        for language in _FENCE_RE.findall(query.lower()):
            language = {"js": "javascript", "ts": "typescript", "pwsh": "powershell"}.get(
                language, language
            )
            votes[language] += 4
            evidence.append(f"code fence -> {language}")
        lowered = query.lower()
        aliases = {
            "python": ("python", "pytest", "pip"),
            "dart": ("dart", "flutter"),
            "javascript": ("javascript", "node", "npm"),
            "typescript": ("typescript", "tsx"),
            "html": ("html", "dom"),
            "powershell": ("powershell", "pwsh"),
            "terminal": ("bash", "shell", "terminal"),
        }
        for language, terms in aliases.items():
            hits = sum(term in lowered for term in terms)
            if hits:
                votes[language] += hits
                evidence.append(f"query mentions {language}")
        if not votes:
            return LanguageSignal("generic", 0.0, ())
        language, count = votes.most_common(1)[0]
        return LanguageSignal(language, count / sum(votes.values()), tuple(evidence))


@dataclass(frozen=True)
class SkillSwapDecision:
    language: LanguageSignal
    previous_skill: str | None
    active_skill: str | None
    changed: bool
    match: SkillMatch | None
    reasons: tuple[str, ...]


class SkillHotSwapController:
    def __init__(self, registry: SkillRegistry, *, hysteresis: float = 4.0) -> None:
        self.registry = registry
        self.hysteresis = hysteresis
        self.detector = LanguageDetector()
        self.active_skill: str | None = None
        self.active_score = 0.0

    def choose(self, query: str, paths: Iterable[str] = ()) -> SkillSwapDecision:
        signal = self.detector.detect(query, paths)
        augmented = f"{query} {signal.language}" if signal.language != "generic" else query
        matches = self.registry.find(augmented, limit=3)
        best = matches[0] if matches else None
        previous = self.active_skill
        changed = False
        reasons = []
        if best is None:
            reasons.append("no matching skill discovered")
        elif previous is None:
            self.active_skill = best.skill.name
            self.active_score = best.score
            changed = True
            reasons.append("initial skill selected")
        elif best.skill.name == previous:
            self.active_score = best.score
            reasons.append("current skill remains the strongest match")
        elif best.score >= self.active_score + self.hysteresis or signal.confidence >= 0.60:
            self.active_skill = best.skill.name
            self.active_score = best.score
            changed = True
            reasons.append("task language or relevance changed materially")
        else:
            reasons.append("hysteresis retained current skill")
        return SkillSwapDecision(
            signal, previous, self.active_skill, changed, best, tuple(reasons)
        )


@dataclass(frozen=True)
class WorkflowRecord:
    task: str
    language: str
    commands: tuple[str, ...]
    checks: tuple[str, ...]
    success: bool
    risk_notes: tuple[str, ...] = ()
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class WorkflowMemory:
    def __init__(self, records: Iterable[WorkflowRecord] = ()) -> None:
        self.records = list(records)

    def add(self, record: WorkflowRecord) -> None:
        self.records.append(record)

    def successful_cluster(self, language: str) -> list[WorkflowRecord]:
        return [
            record for record in self.records
            if record.success and record.language.lower() == language.lower()
        ]

    def save(self, path: str | Path) -> None:
        Path(path).write_text(
            json.dumps([asdict(record) for record in self.records], indent=2),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: str | Path) -> "WorkflowMemory":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(WorkflowRecord(**item) for item in payload)


@dataclass(frozen=True)
class DraftSkill:
    name: str
    content: str
    evidence_count: int
    ready: bool


class SkillDraftAuthor:
    def __init__(self, *, minimum_successes: int = 3) -> None:
        if minimum_successes < 2:
            raise ValueError("minimum_successes must be at least two")
        self.minimum_successes = minimum_successes

    def draft(self, name: str, language: str, records: Iterable[WorkflowRecord]) -> DraftSkill:
        successful = [
            record for record in records
            if record.success and record.language.lower() == language.lower()
        ]
        ready = len(successful) >= self.minimum_successes
        commands = self._common(successful, "commands")
        checks = self._common(successful, "checks")
        risks = self._common(successful, "risk_notes")
        content = (
            "---\n"
            f"name: {name}\n"
            f"description: Repeated successful {language} workflow for {name.replace('-', ' ')}.\n"
            f"tags: [{language}, generated, workflow]\n"
            "---\n# Workflow\n"
            + ("\n".join(f"1. `{item}`" for item in commands) or "1. Reproduce the proven workflow manually.")
            + "\n\n# Validation\n"
            + ("\n".join(f"- `{item}`" for item in checks) or "- Run the narrowest relevant validation.")
            + "\n\n# Safety\n"
            + ("\n".join(f"- {item}" for item in risks) or "- Preview writes and gate destructive or network actions.")
            + "\n"
        )
        return DraftSkill(name, content, len(successful), ready)

    def write(self, draft: DraftSkill, root: str | Path, *, allow_write: bool = False) -> Path:
        if not draft.ready:
            raise ValueError("draft does not have enough successful evidence")
        path = Path(root) / draft.name / "SKILL.md"
        if allow_write:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(draft.content, encoding="utf-8")
        return path

    @staticmethod
    def _common(records: list[WorkflowRecord], field_name: str) -> tuple[str, ...]:
        counter: Counter[str] = Counter()
        for record in records:
            counter.update(getattr(record, field_name))
        threshold = max(2, len(records) // 2 + len(records) % 2)
        return tuple(
            item for item, count in sorted(counter.items(), key=lambda pair: (-pair[1], pair[0]))
            if count >= threshold
        )
