"""Counterfactual test selection for surgical edits."""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

from aider.innovation_context import query_terms

_IMPORT_RE = re.compile(r"^(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))", re.MULTILINE)


@dataclass(frozen=True)
class TestCandidate:
    path: str
    score: float
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class TestSelection:
    candidates: tuple[TestCandidate, ...]
    command: str
    omitted: int = 0


class CounterfactualTestSelector:
    """Rank tests likely to distinguish the proposed patch from leaving code unchanged."""

    def select(
        self,
        *,
        changed_paths: Iterable[str],
        changed_symbols: Iterable[str] = (),
        tests: Mapping[str, str],
        failure_history: Iterable[str] = (),
        limit: int = 8,
    ) -> TestSelection:
        paths = tuple(str(Path(path)) for path in changed_paths)
        symbols = tuple(changed_symbols)
        history = {str(Path(path)) for path in failure_history}
        module_terms = set()
        path_terms = set()
        for path in paths:
            module_terms.add(_module_name(path))
            module_terms.add(Path(path).stem)
            path_terms |= query_terms(path)
        symbol_terms = set()
        for symbol in symbols:
            symbol_terms |= query_terms(symbol)
            symbol_terms.add(symbol.rsplit(".", 1)[-1].lower())

        candidates = []
        for raw_path, text in tests.items():
            path = str(Path(raw_path))
            lower = text.lower()
            score = 0.0
            reasons = []
            imports = {part for match in _IMPORT_RE.findall(text) for part in match if part}
            import_hits = sum(
                1
                for module in module_terms
                if module
                and any(
                    item == module or item.startswith(module + ".")
                    for item in imports
                )
            )
            if import_hits:
                score += import_hits * 12.0
                reasons.append(f"{import_hits} changed-module imports")
            referenced = sorted(term for term in symbol_terms if term and term in lower)
            if referenced:
                score += min(30.0, len(referenced) * 10.0)
                reasons.append("references " + ", ".join(referenced[:4]))
            overlap = len(query_terms(path) & path_terms)
            if overlap:
                score += overlap * 4.0
                reasons.append("path affinity")
            if path in history:
                score += 18.0
                reasons.append("recently failed")
            if any(Path(changed).stem in Path(path).stem for changed in paths):
                score += 8.0
                reasons.append("paired test filename")
            if score > 0:
                candidates.append(TestCandidate(path, round(score, 3), tuple(reasons)))

        candidates.sort(key=lambda item: (-item.score, item.path))
        selected = tuple(candidates[:limit])
        command = "pytest -q " + " ".join(shlex.quote(item.path) for item in selected)
        return TestSelection(selected, command.rstrip(), max(0, len(candidates) - len(selected)))


def _module_name(path: str) -> str:
    value = str(Path(path).with_suffix("")).replace("\\", "/")
    return ".".join(part for part in value.split("/") if part not in {"", "."})
