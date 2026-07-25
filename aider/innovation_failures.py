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
        test = _TEST_RE.search(output)
        assertion = _ASSERT_RE.search(output)
        messages = _ERROR_RE.findall(output)
        frames = tuple(
            FailureFrame(match.group("path"), int(match.group("line")), match.group("function").strip())
            for match in _FRAME_RE.finditer(output)
        )
        excerpt = [
            line
            for line in output.splitlines()
            if line.startswith(("E ", "> ")) or ".py:" in line or "FAILED " in line
        ]
        failed = re.search(r"FAILED\s+[^:]+::(?P<name>test_[^\s]+)", output)
        return LocalizedFailure(
            test.group("name") if test else failed.group("name") if failed else "",
            messages[-1].strip() if messages else "",
            assertion.group("assertion").strip() if assertion else "",
            frames,
            "\n".join(excerpt[-24:]),
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
        slices = []
        consumed = estimate_tokens(failure.raw_excerpt) + 80
        seen = set()
        for frame in sorted(failure.frames, key=lambda item: (self._is_test(item.path), -item.line)):
            path = self._resolve(frame.path, normalized)
            if path is None:
                continue
            lines = normalized[path].splitlines()
            start = max(1, frame.line - context_lines)
            end = min(len(lines), frame.line + context_lines)
            key = (path, start, end)
            if key in seen:
                continue
            numbered = "\n".join(
                f"{number:>6} | {line}"
                for number, line in enumerate(lines[start - 1 : end], start=start)
            )
            cost = estimate_tokens(numbered) + 18
            if len(slices) >= max_slices or consumed + cost > max_tokens:
                continue
            slices.append(
                RepairSlice(path, start, end, numbered, f"trace frame {frame.function} at line {frame.line}")
            )
            consumed += cost
            seen.add(key)
        return FailureRepairPacket(failure, tuple(slices), consumed, max_tokens)

    @staticmethod
    def _resolve(path: str, files: Mapping[str, str]) -> str | None:
        normalized = str(Path(path))
        if normalized in files:
            return normalized
        suffix = [candidate for candidate in files if candidate.endswith(normalized)]
        if len(suffix) == 1:
            return suffix[0]
        name = Path(path).name
        matches = [candidate for candidate in files if Path(candidate).name == name]
        return matches[0] if len(matches) == 1 else None

    @staticmethod
    def _is_test(path: str) -> bool:
        value = path.replace("\\", "/").lower()
        return "/test" in value or Path(value).name.startswith("test_")
