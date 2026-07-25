"""Adaptive, query-focused context packets for small-context coding models."""

from __future__ import annotations

import math
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping, Sequence

_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_./:-]{1,}")
_SYMBOL_RE = re.compile(
    r"^\s*(?:async\s+def|def|class|function|interface|enum|struct|fn)\s+([A-Za-z_$][\w$]*)",
    re.MULTILINE,
)


def estimate_tokens(text: str, chars_per_token: float = 4.0) -> int:
    if not text:
        return 0
    return max(1, math.ceil(len(text) / max(chars_per_token, 1.0)))


def query_terms(query: str) -> set[str]:
    return {match.group(0).lower() for match in _WORD_RE.finditer(query)}


@dataclass(frozen=True)
class ContextSlice:
    path: str
    start_line: int
    end_line: int
    text: str
    score: float
    reasons: tuple[str, ...] = ()

    @property
    def token_estimate(self) -> int:
        return estimate_tokens(self.text)


@dataclass(frozen=True)
class ContextPacket:
    query: str
    slices: tuple[ContextSlice, ...]
    token_estimate: int
    budget: int
    omitted_candidates: int = 0

    def render(self) -> str:
        header = (
            f"# Adaptive context packet\n"
            f"# query: {self.query}\n"
            f"# estimated tokens: {self.token_estimate}/{self.budget}\n"
        )
        blocks = []
        for item in self.slices:
            reason_text = ", ".join(item.reasons) or "relevance"
            blocks.append(
                f"\n## {item.path}:{item.start_line}-{item.end_line} "
                f"(score={item.score:.2f}; {reason_text})\n{item.text.rstrip()}\n"
            )
        return header + "".join(blocks)


@dataclass
class FloatingContextState:
    pinned_paths: set[str] = field(default_factory=set)
    touched_paths: list[str] = field(default_factory=list)
    facts: dict[str, str] = field(default_factory=dict)

    def pin(self, path: str) -> None:
        self.pinned_paths.add(path)

    def unpin(self, path: str) -> None:
        self.pinned_paths.discard(path)

    def touch(self, path: str, history_limit: int = 32) -> None:
        if path in self.touched_paths:
            self.touched_paths.remove(path)
        self.touched_paths.append(path)
        del self.touched_paths[:-history_limit]

    def remember(self, key: str, value: str) -> None:
        self.facts[key] = value


class FloatingContextManager:
    def __init__(
        self,
        max_tokens: int = 4096,
        reserve_tokens: int = 768,
        chunk_lines: int = 80,
        overlap_lines: int = 12,
        max_slices_per_file: int = 3,
        state: FloatingContextState | None = None,
    ) -> None:
        if max_tokens <= reserve_tokens:
            raise ValueError("max_tokens must be larger than reserve_tokens")
        if chunk_lines < 8:
            raise ValueError("chunk_lines must be at least 8")
        if not 0 <= overlap_lines < chunk_lines:
            raise ValueError("overlap_lines must be smaller than chunk_lines")
        self.max_tokens = max_tokens
        self.reserve_tokens = reserve_tokens
        self.chunk_lines = chunk_lines
        self.overlap_lines = overlap_lines
        self.max_slices_per_file = max_slices_per_file
        self.state = state or FloatingContextState()

    @property
    def context_budget(self) -> int:
        return self.max_tokens - self.reserve_tokens

    def build_packet(
        self,
        files: Mapping[str, str],
        query: str,
        *,
        changed_paths: Iterable[str] = (),
        explicit_paths: Iterable[str] = (),
    ) -> ContextPacket:
        terms = query_terms(query)
        changed = {str(Path(path)) for path in changed_paths}
        explicit = {str(Path(path)) for path in explicit_paths}
        candidates: list[ContextSlice] = []

        for raw_path, text in files.items():
            path = str(Path(raw_path))
            candidates.extend(self._chunk_and_score(path, text, terms, changed, explicit))

        candidates.sort(key=lambda item: (-item.score, item.path, item.start_line))
        chosen: list[ContextSlice] = []
        per_file: defaultdict[str, int] = defaultdict(int)
        consumed = estimate_tokens(query) + 48

        for candidate in candidates:
            if per_file[candidate.path] >= self.max_slices_per_file:
                continue
            cost = candidate.token_estimate + 20
            if consumed + cost > self.context_budget:
                continue
            if self._overlaps_existing(candidate, chosen):
                continue
            chosen.append(candidate)
            consumed += cost
            per_file[candidate.path] += 1

        return ContextPacket(
            query=query,
            slices=tuple(chosen),
            token_estimate=consumed,
            budget=self.context_budget,
            omitted_candidates=max(0, len(candidates) - len(chosen)),
        )

    def _chunk_and_score(
        self,
        path: str,
        text: str,
        terms: set[str],
        changed: set[str],
        explicit: set[str],
    ) -> list[ContextSlice]:
        lines = text.splitlines()
        if not lines:
            return []
        step = self.chunk_lines - self.overlap_lines
        output: list[ContextSlice] = []
        path_terms = query_terms(path)
        symbols = {name.lower() for name in _SYMBOL_RE.findall(text)}

        for start in range(0, len(lines), step):
            end = min(len(lines), start + self.chunk_lines)
            chunk_lines = lines[start:end]
            chunk = "\n".join(
                f"{line_no:>6} | {line}"
                for line_no, line in enumerate(chunk_lines, start=start + 1)
            )
            chunk_lower = chunk.lower()
            score = 0.0
            reasons: list[str] = []

            matches = sum(chunk_lower.count(term) for term in terms)
            if matches:
                score += min(24.0, 2.5 * matches)
                reasons.append(f"{matches} query hits")

            filename_hits = len(terms & path_terms)
            if filename_hits:
                score += 7.0 * filename_hits
                reasons.append("path match")

            symbol_hits = len(terms & symbols)
            if symbol_hits:
                score += 9.0 * symbol_hits
                reasons.append("symbol match")

            if path in explicit:
                score += 30.0
                reasons.append("explicit file")
            if path in changed:
                score += 18.0
                reasons.append("changed file")
            if path in self.state.pinned_paths:
                score += 22.0
                reasons.append("pinned")
            if path in self.state.touched_paths:
                distance = len(self.state.touched_paths) - self.state.touched_paths.index(path)
                score += max(2.0, 10.0 - distance * 0.5)
                reasons.append("recently touched")

            structural_lines = sum(
                1
                for line in chunk_lines
                if re.match(r"^\s*(?:class|def|async\s+def|function|interface)\b", line)
            )
            if structural_lines:
                score += min(4.0, structural_lines * 0.75)
                reasons.append("structural context")

            if not terms:
                score += 1.0
            if score <= 0:
                continue
            output.append(
                ContextSlice(
                    path=path,
                    start_line=start + 1,
                    end_line=end,
                    text=chunk,
                    score=score,
                    reasons=tuple(reasons),
                )
            )
            if end == len(lines):
                break

        return output

    @staticmethod
    def _overlaps_existing(candidate: ContextSlice, chosen: Sequence[ContextSlice]) -> bool:
        for existing in chosen:
            if existing.path != candidate.path:
                continue
            if candidate.start_line <= existing.end_line and existing.start_line <= candidate.end_line:
                return True
        return False
