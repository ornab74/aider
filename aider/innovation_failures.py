"""Failure-localized retry packets for pytest and Python tracebacks."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from aider.innovation_context import estimate_tokens

_FRAME_RE = re.compile(
    r"^(?P<path>[^\n:]+\.py):(?P<line>\d+):\s+in\s+(?P<function>[^\n]+)$",
    re.MULTILINE,
)
_TEST_RE = re.compile(r"_{2,}\s*(?P<name>test_[A-Za-z0-9_\[\]-]+)\s*_{2,}")
_ASSERT_RE = re.compile(r"^>\s*(?P<assertion>assert\s+.+)$", re.MULTILINE)
_ERROR_RE = re.compile(r"^E\s+(?P<message>.+)$", re.MULTILINE)


@dataclass(frozen=True)
class FailureFrame:
    path: str
    line: int
    function: str


@dataclass(frozen=True)
class LocalizedFailure:
    test_name: str
    message: str
    assertion: str
    frames: tuple[FailureFrame, ...]
    raw_excerpt: str


@dataclass(frozen=True)
class RepairSlice:
    path: str
    start_line: int
    end_line: int
    text: str
    reason: str


@dataclass(frozen=True)
class FailureRepairPacket:
    failure: LocalizedFailure
    slices: tuple[RepairSlice, ...]
    token_estimate: int
    budget: int

    def render(self) -> str:
        lines = [
            "# Failure-localized repair packet",
            f"# test: {self.failure.test_name or 'unknown'}",
            f"# message: {self.failure.message or 'unknown'}",
            f"# estimated tokens: {self.token_estimate}/{self.budget}",
        ]
        if self.failure.assertion:
            lines.append(f"# assertion: {self.failure.assertion}")
        for item in self.slices:
            lines.append(
                f"\n## {item.path}:{item.start_line}-{item.end_line} ({item.reason})\n"
                f"{item.text.rstrip()}"
            )
        return "\n".join(lines) + "\n"


class FailureLocalizer:
    def parse(self, output: str) -> LocalizedFailure:
        test_match = _TEST_RE.search(output)
        assertion_match = _ASSERT_RE.search(output)
        messages = _ERROR_RE.findall(output)
        frames = tuple(
            FailureFrame(
                match.group("path"),
                int(match.group("line")),
                match.group("function").strip(),
            )
            for match in _FRAME_RE.finditer(output)
        )
        excerpt_lines = []
        for line in output.splitlines():
            if line.startswith(("E ", "> ")) or ".py:" in line or "FAILED " in line:
                excerpt_lines.append(line)
        return LocalizedFailure(
            test_name=test_match.group("name") if test_match else self._failed_name(output),
            message=messages[-1].strip() if messages else self._fallback_message(output),
            assertion=assertion_match.group("assertion").strip() if assertion_match else "",
            frames=frames,
            raw_excerpt="\n".join(excerpt_lines[-24:]),
        )

    def build_packet(
        self,
        failure: LocalizedFailure,
        files: Mapping[str, str],
        *,
        context_lines: int = 14,
        max_tokens: int = 1600,
        max_slices: int = 5,
    ) -> FailureRepairPacket:
        normalized = {str(Path(path)): text for path, text in files.items()}
        slices: list[RepairSlice] = []
        consumed = estimate_tokens(failure.raw_excerpt) + 80
        seen: set[tuple[str, int, int]] = set()

        ordered_frames = sorted(
            failure.frames,
            key=lambda frame: (self._is_test_path(frame.path), -frame.line),
        )
        for frame in ordered_frames:
            resolved = self._resolve_path(frame.path, normalized)
            if resolved is None:
                continue
            text = normalized[resolved]
            lines = text.splitlines()
            start = max(1, frame.line - context_lines)
            end = min(len(lines), frame.line + context_lines)
            key = (resolved, start, end)
            if key in seen:
                continue
            numbered = "\n".join(
                f"{line_no:>6} | {line}"
                for line_no, line in enumerate(lines[start - 1 : end], start=start)
            )
            cost = estimate_tokens(numbered) + 18
            if len(slices) >= max_slices or consumed + cost > max_tokens:
                continue
            reason = f"trace frame {frame.function} at line {frame.line}"
            slices.append(RepairSlice(resolved, start, end, numbered, reason))
            consumed += cost
            seen.add(key)

        return FailureRepairPacket(failure, tuple(slices), consumed, max_tokens)

    @staticmethod
    def _resolve_path(path: str, files: Mapping[str, str]) -> str | None:
        normalized = str(Path(path))
        if normalized in files:
            return normalized
        suffix_matches = [candidate for candidate in files if candidate.endswith(normalized)]
        if len(suffix_matches) == 1:
            return suffix_matches[0]
        basename = Path(path).name
        basename_matches = [candidate for candidate in files if Path(candidate).name == basename]
        return basename_matches[0] if len(basename_matches) == 1 else None

    @staticmethod
    def _is_test_path(path: str) -> bool:
        value = path.replace("\\", "/").lower()
        return "/test" in value or Path(value).name.startswith("test_")

    @staticmethod
    def _failed_name(output: str) -> str:
        match = re.search(r"FAILED\s+[^:]+::(?P<name>test_[^\s]+)", output)
        return match.group("name") if match else ""

    @staticmethod
    def _fallback_message(output: str) -> str:
        for line in reversed(output.splitlines()):
            if "Error" in line or "Exception" in line or "Assertion" in line:
                return line.strip()
        return ""
