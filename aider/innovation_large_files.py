"""Search-first, verification-heavy editing helpers for large source files."""

from __future__ import annotations

import difflib
import hashlib
import os
import re
import shlex
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from aider.innovation_context import estimate_tokens, query_terms

_IDENTIFIER_RE = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]{2,}")


@dataclass(frozen=True)
class LargeFileProfile:
    path: str
    size_bytes: int
    line_count: int
    token_estimate: int
    is_large: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class SearchWindow:
    start_line: int
    end_line: int
    text: str
    matched_terms: tuple[str, ...]


@dataclass(frozen=True)
class SearchPlan:
    path: str
    terms: tuple[str, ...]
    ripgrep_command: str
    sed_preview_command: str


@dataclass(frozen=True)
class SurgicalEdit:
    path: str
    start_line: int
    end_line: int
    expected_sha256: str
    replacement: str


@dataclass(frozen=True)
class EditResult:
    path: str
    changed: bool
    diff: str
    new_sha256: str


class LargeFileDetector:
    def __init__(
        self,
        *,
        byte_threshold: int = 256_000,
        line_threshold: int = 4_000,
        token_threshold: int = 32_000,
    ) -> None:
        self.byte_threshold = byte_threshold
        self.line_threshold = line_threshold
        self.token_threshold = token_threshold

    def profile(self, path: str | Path, text: str | None = None) -> LargeFileProfile:
        file_path = Path(path)
        if text is None:
            text = file_path.read_text(encoding="utf-8", errors="replace")
        size_bytes = len(text.encode("utf-8"))
        line_count = text.count("\n") + (1 if text else 0)
        tokens = estimate_tokens(text)
        reasons = []
        if size_bytes >= self.byte_threshold:
            reasons.append(f"bytes>={self.byte_threshold}")
        if line_count >= self.line_threshold:
            reasons.append(f"lines>={self.line_threshold}")
        if tokens >= self.token_threshold:
            reasons.append(f"tokens>={self.token_threshold}")
        return LargeFileProfile(
            str(file_path), size_bytes, line_count, tokens, bool(reasons), tuple(reasons)
        )


class SearchFirstEditor:
    def build_search_plan(self, path: str | Path, query: str) -> SearchPlan:
        terms = self._search_terms(query)
        quoted_path = shlex.quote(str(path))
        pattern = "|".join(re.escape(term) for term in terms) or re.escape(query.strip())
        return SearchPlan(
            str(path),
            tuple(terms),
            f"rg -n -C 8 --no-heading -e {shlex.quote(pattern)} {quoted_path}",
            f"sed -n '1,220p' {quoted_path}",
        )

    def extract_windows(
        self,
        text: str,
        query: str,
        *,
        context_lines: int = 24,
        max_windows: int = 8,
    ) -> list[SearchWindow]:
        terms = self._search_terms(query)
        if not terms:
            return []
        lines = text.splitlines()
        hits = [
            index
            for index, line in enumerate(lines)
            if any(term.lower() in line.lower() for term in terms)
        ]
        ranges = self._merge_ranges(
            (max(0, hit - context_lines), min(len(lines), hit + context_lines + 1))
            for hit in hits
        )
        windows = []
        for start, end in ranges[:max_windows]:
            numbered = "\n".join(
                f"{line_no:>6} | {line}"
                for line_no, line in enumerate(lines[start:end], start=start + 1)
            )
            matched = tuple(
                term
                for term in terms
                if any(term.lower() in line.lower() for line in lines[start:end])
            )
            windows.append(SearchWindow(start + 1, end, numbered, matched))
        return windows

    def make_edit(
        self,
        path: str | Path,
        text: str,
        *,
        start_line: int,
        end_line: int,
        replacement: str,
    ) -> SurgicalEdit:
        self._validate_range(text, start_line, end_line)
        return SurgicalEdit(
            str(path), start_line, end_line, self.sha256(text), replacement
        )

    def apply(self, edit: SurgicalEdit, *, allow_write: bool = False) -> EditResult:
        path = Path(edit.path)
        original = path.read_text(encoding="utf-8")
        if self.sha256(original) != edit.expected_sha256:
            raise RuntimeError("file changed after the edit was planned; refusing stale edit")
        self._validate_range(original, edit.start_line, edit.end_line)
        old_lines = original.splitlines(keepends=True)
        replacement = edit.replacement
        if replacement and not replacement.endswith("\n"):
            replacement += "\n"
        new_lines = (
            old_lines[: edit.start_line - 1]
            + replacement.splitlines(keepends=True)
            + old_lines[edit.end_line :]
        )
        updated = "".join(new_lines)
        diff = "".join(
            difflib.unified_diff(
                original.splitlines(keepends=True),
                updated.splitlines(keepends=True),
                fromfile=str(path),
                tofile=str(path),
            )
        )
        if allow_write and updated != original:
            self._atomic_write(path, updated)
        return EditResult(str(path), updated != original, diff, self.sha256(updated))

    def build_verified_sed_script(self, edit: SurgicalEdit) -> str:
        replacement = edit.replacement.replace("\\", "\\\\").replace("\n", "\\\n")
        expression = f"{edit.start_line},{edit.end_line}c\\\n{replacement}"
        return (
            "set -euo pipefail\n"
            f"file={shlex.quote(edit.path)}\n"
            f"expected={shlex.quote(edit.expected_sha256)}\n"
            "actual=$(sha256sum \"$file\" | awk '{print $1}')\n"
            "[ \"$actual\" = \"$expected\" ] || exit 2\n"
            "cp -- \"$file\" \"$file.aider-bak\"\n"
            f"sed -i {shlex.quote(expression)} \"$file\"\n"
            "diff -u \"$file.aider-bak\" \"$file\" || true\n"
        )

    @staticmethod
    def sha256(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    @staticmethod
    def _search_terms(query: str) -> list[str]:
        terms = list(query_terms(query))
        identifiers = [match.group(0).lower() for match in _IDENTIFIER_RE.finditer(query)]
        ranked = sorted(set(terms + identifiers), key=lambda item: (-len(item), item))
        stop = {"change", "update", "modify", "please", "file", "code", "with", "from"}
        return [term for term in ranked if term not in stop][:10]

    @staticmethod
    def _merge_ranges(ranges: Iterable[tuple[int, int]]) -> list[tuple[int, int]]:
        merged: list[list[int]] = []
        for start, end in sorted(ranges):
            if not merged or start > merged[-1][1]:
                merged.append([start, end])
            else:
                merged[-1][1] = max(merged[-1][1], end)
        return [(start, end) for start, end in merged]

    @staticmethod
    def _validate_range(text: str, start_line: int, end_line: int) -> None:
        line_count = text.count("\n") + (1 if text else 0)
        if start_line < 1 or end_line < start_line or end_line > line_count:
            raise ValueError(f"invalid line range {start_line}-{end_line} for {line_count} lines")

    @staticmethod
    def _atomic_write(path: Path, text: str) -> None:
        fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
