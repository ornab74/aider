"""Large-diff folding and reversible diff inversion helpers."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

_FILE_RE = re.compile(r"^diff --git a/(?P<old>.+) b/(?P<new>.+)$")
_HUNK_RE = re.compile(
    r"^@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? "
    r"\+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@(?P<tail>.*)$"
)


@dataclass(frozen=True)
class FoldedFileDiff:
    path: str
    hunks: int
    additions: int
    deletions: int
    repeated_additions: tuple[tuple[str, int], ...]
    repeated_deletions: tuple[tuple[str, int], ...]


@dataclass(frozen=True)
class FoldedDiffReport:
    files: tuple[FoldedFileDiff, ...]
    total_additions: int
    total_deletions: int
    total_hunks: int

    def render(self) -> str:
        lines = [
            "# Folded unified diff",
            f"# files={len(self.files)} hunks={self.total_hunks} ",
            f"# additions={self.total_additions} deletions={self.total_deletions}",
        ]
        for item in self.files:
            lines.append(
                f"\n## {item.path}: {item.hunks} hunks, +{item.additions}/-{item.deletions}"
            )
            if item.repeated_additions:
                lines.append("Repeated additions:")
                lines.extend(f"- {count}x {line}" for line, count in item.repeated_additions)
            if item.repeated_deletions:
                lines.append("Repeated deletions:")
                lines.extend(f"- {count}x {line}" for line, count in item.repeated_deletions)
        return "\n".join(lines) + "\n"


class LargeDiffFolder:
    def fold(self, diff: str, *, repeat_threshold: int = 3) -> FoldedDiffReport:
        current = "(unknown)"
        stats: dict[str, dict[str, object]] = {}
        for line in diff.splitlines():
            file_match = _FILE_RE.match(line)
            if file_match:
                current = file_match.group("new")
                stats.setdefault(current, self._empty_stats())
                continue
            item = stats.setdefault(current, self._empty_stats())
            if line.startswith("@@"):
                item["hunks"] = int(item["hunks"]) + 1
            elif line.startswith("+") and not line.startswith("+++"):
                item["additions"] = int(item["additions"]) + 1
                item["added"][_normalize_line(line[1:])] += 1
            elif line.startswith("-") and not line.startswith("---"):
                item["deletions"] = int(item["deletions"]) + 1
                item["deleted"][_normalize_line(line[1:])] += 1

        files = []
        for path, item in sorted(stats.items()):
            repeated_additions = tuple(
                (line, count)
                for line, count in item["added"].most_common()
                if line and count >= repeat_threshold
            )
            repeated_deletions = tuple(
                (line, count)
                for line, count in item["deleted"].most_common()
                if line and count >= repeat_threshold
            )
            files.append(
                FoldedFileDiff(
                    path,
                    int(item["hunks"]),
                    int(item["additions"]),
                    int(item["deletions"]),
                    repeated_additions,
                    repeated_deletions,
                )
            )
        return FoldedDiffReport(
            tuple(files),
            sum(item.additions for item in files),
            sum(item.deletions for item in files),
            sum(item.hunks for item in files),
        )

    @staticmethod
    def _empty_stats() -> dict[str, object]:
        return {
            "hunks": 0,
            "additions": 0,
            "deletions": 0,
            "added": Counter(),
            "deleted": Counter(),
        }


def invert_unified_diff(diff: str) -> str:
    """Return a unified diff that reverses the supplied transformation."""

    output = []
    old_path = ""
    new_path = ""
    for line in diff.splitlines(keepends=True):
        stripped = line.rstrip("\r\n")
        ending = line[len(stripped) :]
        match = _FILE_RE.match(stripped)
        if match:
            old_path = match.group("old")
            new_path = match.group("new")
            output.append(f"diff --git a/{new_path} b/{old_path}{ending}")
            continue
        if stripped.startswith("--- "):
            path = f"b/{new_path}" if new_path else stripped[4:]
            output.append(f"--- {path}{ending}")
            continue
        if stripped.startswith("+++ "):
            path = f"a/{old_path}" if old_path else stripped[4:]
            output.append(f"+++ {path}{ending}")
            continue
        hunk = _HUNK_RE.match(stripped)
        if hunk:
            old_count = (
                f",{hunk.group('old_count')}" if hunk.group("old_count") else ""
            )
            new_count = (
                f",{hunk.group('new_count')}" if hunk.group("new_count") else ""
            )
            output.append(
                f"@@ -{hunk.group('new_start')}{new_count} "
                f"+{hunk.group('old_start')}{old_count} "
                f"@@{hunk.group('tail')}{ending}"
            )
            continue
        if stripped.startswith("+") and not stripped.startswith("+++"):
            output.append("-" + stripped[1:] + ending)
        elif stripped.startswith("-") and not stripped.startswith("---"):
            output.append("+" + stripped[1:] + ending)
        else:
            output.append(line)
    return "".join(output)


def _normalize_line(line: str) -> str:
    return re.sub(r"\s+", " ", line.strip())
