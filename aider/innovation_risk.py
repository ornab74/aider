"""Blast-radius scoring and independent patch quorum for risky edits."""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import dataclass


@dataclass(frozen=True)
class FileImpact:
    path: str
    additions: int
    deletions: int
    critical: bool
    manifest: bool
    tests: bool


@dataclass(frozen=True)
class BlastRadiusReport:
    score: int
    band: str
    impacts: tuple[FileImpact, ...]
    reasons: tuple[str, ...]
    requires_quorum: bool
    minimum_approvals: int


class BlastRadiusAnalyzer:
    CRITICAL = re.compile(r"(?:auth|security|crypto|payment|deploy|migration|permissions?)", re.I)
    MANIFEST = re.compile(r"(?:pyproject\.toml|requirements.*|package-lock\.json|Dockerfile|\.github/)")
    TEST = re.compile(r"(?:^|/)(?:tests?|specs?)(?:/|_)|(?:test|spec)\.", re.I)
    PUBLIC_API = re.compile(r"^[+-]\s*(?:async\s+def|def|class|function|interface)\s+[^_]", re.M)

    def analyze(self, diff: str) -> BlastRadiusReport:
        impacts: list[FileImpact] = []
        current = None
        additions = deletions = 0

        def flush() -> None:
            nonlocal current, additions, deletions
            if current:
                impacts.append(
                    FileImpact(
                        current,
                        additions,
                        deletions,
                        bool(self.CRITICAL.search(current)),
                        bool(self.MANIFEST.search(current)),
                        bool(self.TEST.search(current)),
                    )
                )
            current = None
            additions = deletions = 0

        for line in diff.splitlines():
            if line.startswith("diff --git "):
                flush()
                parts = line.split()
                current = parts[3][2:] if len(parts) >= 4 else "unknown"
            elif current and line.startswith("+") and not line.startswith("+++"):
                additions += 1
            elif current and line.startswith("-") and not line.startswith("---"):
                deletions += 1
        flush()

        changed = sum(item.additions + item.deletions for item in impacts)
        score = min(35, changed // 8) + min(20, max(0, len(impacts) - 1) * 4)
        reasons = []
        critical_count = sum(item.critical for item in impacts)
        manifest_count = sum(item.manifest for item in impacts)
        test_count = sum(item.tests for item in impacts)
        api_changes = len(self.PUBLIC_API.findall(diff))
        if critical_count:
            score += 25
            reasons.append(f"{critical_count} security/deployment-sensitive files")
        if manifest_count:
            score += 15
            reasons.append(f"{manifest_count} dependency or deployment manifests")
        if api_changes:
            score += min(20, api_changes * 8)
            reasons.append(f"{api_changes} public API declarations changed")
        if impacts and not test_count:
            score += 10
            reasons.append("source changed without accompanying tests")
        score = min(100, score)
        band = "critical" if score >= 75 else "high" if score >= 45 else "medium" if score >= 20 else "low"
        approvals = 3 if band == "critical" else 2 if band == "high" else 1
        return BlastRadiusReport(score, band, tuple(impacts), tuple(reasons), approvals > 1, approvals)


class PatchQuorum:
    def __init__(self) -> None:
        self.votes: dict[str, str] = {}

    def submit(self, reviewer: str, patch: str) -> str:
        digest = hashlib.sha256(self._normalize(patch).encode()).hexdigest()
        self.votes[reviewer] = digest
        return digest

    def reached(self, minimum_approvals: int) -> bool:
        counts = Counter(self.votes.values())
        return bool(counts and max(counts.values()) >= minimum_approvals)

    @staticmethod
    def _normalize(patch: str) -> str:
        return "\n".join(line.rstrip() for line in patch.strip().splitlines())
