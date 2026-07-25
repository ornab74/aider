"""Edit blast-radius analysis and independent patch quorum tracking."""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import PurePosixPath

_DIFF_FILE_RE = re.compile(r"^diff --git a/(.+?) b/(.+)$")
_HUNK_RE = re.compile(r"^@@")
_PUBLIC_API_RE = re.compile(
    r"^\s*(?:export\s+|public\s+)?(?:async\s+)?(?:def|class|interface|function|fn|func)\b"
)
_CRITICAL_RE = re.compile(
    r"(?:^|/)(?:auth|security|crypto|payments?|billing|migrations?|schemas?|deploy|infra)(?:/|$)",
    re.IGNORECASE,
)
_TEST_RE = re.compile(r"(?:^|/)(?:tests?|specs?)(?:/|$)|(?:^|[._-])test(?:[._-]|$)", re.IGNORECASE)
_GENERATED_RE = re.compile(
    r"(?:^|/)(?:vendor|dist|build|generated)(?:/|$)|(?:\.min\.js$|\.lock$)", re.IGNORECASE
)
_DEPENDENCY_FILES = {
    "pyproject.toml",
    "requirements.txt",
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "cargo.toml",
    "cargo.lock",
    "go.mod",
    "go.sum",
}


@dataclass(frozen=True)
class FileImpact:
    path: str
    additions: int
    deletions: int
    hunks: int
    critical: bool
    test_file: bool
    generated: bool
    dependency_config: bool
    public_api_lines: int

    @property
    def changed_lines(self) -> int:
        return self.additions + self.deletions


@dataclass(frozen=True)
class BlastRadiusReport:
    score: int
    band: str
    impacts: tuple[FileImpact, ...]
    minimum_approvals: int
    reasons: tuple[str, ...]

    @property
    def requires_quorum(self) -> bool:
        return self.minimum_approvals > 1


class BlastRadiusAnalyzer:
    def analyze(self, diff: str) -> BlastRadiusReport:
        impacts = self._parse(diff)
        if not impacts:
            return BlastRadiusReport(0, "none", (), 1, ("no changed files detected",))

        changed_lines = sum(item.changed_lines for item in impacts)
        source_impacts = [item for item in impacts if not item.test_file and not item.generated]
        score = min(24, len(impacts) * 4)
        score += min(28, round(changed_lines / 8))
        reasons = [f"{len(impacts)} files and {changed_lines} changed lines"]

        critical_count = sum(item.critical for item in impacts)
        if critical_count:
            score += min(30, 18 + 4 * critical_count)
            reasons.append(f"{critical_count} security/deployment-sensitive files")

        dependency_count = sum(item.dependency_config for item in impacts)
        if dependency_count:
            score += min(18, 10 + 3 * dependency_count)
            reasons.append(f"{dependency_count} dependency or build manifests")

        public_api_lines = sum(item.public_api_lines for item in impacts)
        if public_api_lines:
            score += min(18, 6 + public_api_lines * 2)
            reasons.append(f"{public_api_lines} public API declarations changed")

        has_tests = any(item.test_file for item in impacts)
        if source_impacts and not has_tests:
            score += 10
            reasons.append("source changed without accompanying tests")

        generated_lines = sum(item.changed_lines for item in impacts if item.generated)
        if generated_lines and generated_lines == changed_lines:
            score -= 8
            reasons.append("changes are confined to generated or vendored files")

        score = min(100, max(0, score))
        if score < 25:
            band, approvals = "low", 1
        elif score < 45:
            band, approvals = "medium", 1
        elif score < 75:
            band, approvals = "high", 2
        else:
            band, approvals = "critical", 3
        return BlastRadiusReport(score, band, tuple(impacts), approvals, tuple(reasons))

    @staticmethod
    def _parse(diff: str) -> list[FileImpact]:
        records: list[dict[str, object]] = []
        current: dict[str, object] | None = None
        for line in diff.splitlines():
            match = _DIFF_FILE_RE.match(line)
            if match:
                if current:
                    records.append(current)
                path = match.group(2)
                name = PurePosixPath(path).name.lower()
                current = {
                    "path": path,
                    "additions": 0,
                    "deletions": 0,
                    "hunks": 0,
                    "critical": bool(_CRITICAL_RE.search(path))
                    or path.startswith(".github/workflows/"),
                    "test_file": bool(_TEST_RE.search(path)),
                    "generated": bool(_GENERATED_RE.search(path)),
                    "dependency_config": name in _DEPENDENCY_FILES,
                    "public_api_lines": 0,
                }
                continue
            if not current:
                continue
            if _HUNK_RE.match(line):
                current["hunks"] = int(current["hunks"]) + 1
            elif line.startswith("+") and not line.startswith("+++"):
                current["additions"] = int(current["additions"]) + 1
                if _PUBLIC_API_RE.match(line[1:]):
                    current["public_api_lines"] = int(current["public_api_lines"]) + 1
            elif line.startswith("-") and not line.startswith("---"):
                current["deletions"] = int(current["deletions"]) + 1
                if _PUBLIC_API_RE.match(line[1:]):
                    current["public_api_lines"] = int(current["public_api_lines"]) + 1
        if current:
            records.append(current)
        return [FileImpact(**record) for record in records]


class PatchQuorum:
    """Require independent actors to agree on an identical normalized patch."""

    def __init__(self) -> None:
        self._votes: dict[str, str] = {}

    def submit(self, actor: str, patch: str) -> str:
        if not actor.strip():
            raise ValueError("actor is required")
        digest = self.digest(patch)
        self._votes[actor] = digest
        return digest

    def reached(self, minimum_approvals: int) -> bool:
        if minimum_approvals < 1:
            raise ValueError("minimum_approvals must be positive")
        return any(count >= minimum_approvals for count in Counter(self._votes.values()).values())

    def winning_digest(self) -> str | None:
        if not self._votes:
            return None
        return Counter(self._votes.values()).most_common(1)[0][0]

    @staticmethod
    def digest(patch: str) -> str:
        normalized = "\n".join(line.rstrip() for line in patch.strip().splitlines()) + "\n"
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
