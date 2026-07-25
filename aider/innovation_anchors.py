"""Formatting-resilient symbol anchors for surgical source edits."""

from __future__ import annotations

import ast
import hashlib
import re
from dataclasses import dataclass
from typing import Iterable

_GENERIC_SYMBOL_RE = re.compile(
    r"^(?P<indent>\s*)(?P<kind>async\s+def|def|class|function|interface|enum|struct|fn)"
    r"\s+(?P<name>[A-Za-z_$][\w$]*)",
    re.MULTILINE,
)


@dataclass(frozen=True)
class SymbolAnchor:
    path: str
    qualified_name: str
    kind: str
    start_line: int
    end_line: int
    exact_fingerprint: str
    structural_fingerprint: str


@dataclass(frozen=True)
class AnchorResolution:
    anchor: SymbolAnchor
    matched: SymbolAnchor | None
    confidence: float
    reason: str


class SymbolAnchorIndex:
    def index(self, text: str, *, path: str = "") -> list[SymbolAnchor]:
        try:
            tree = ast.parse(text)
        except SyntaxError:
            return self._generic_index(text, path=path)
        output: list[SymbolAnchor] = []
        self._walk_python(tree.body, output, path=path, parents=())
        return output

    def create(self, text: str, symbol: str, *, path: str = "") -> SymbolAnchor:
        candidates = self.index(text, path=path)
        exact = [item for item in candidates if item.qualified_name == symbol]
        if not exact:
            exact = [
                item
                for item in candidates
                if item.qualified_name.rsplit(".", 1)[-1] == symbol
            ]
        if len(exact) != 1:
            raise ValueError(f"symbol {symbol!r} resolved to {len(exact)} anchors")
        return exact[0]

    def resolve(self, text: str, anchor: SymbolAnchor) -> AnchorResolution:
        candidates = [
            item
            for item in self.index(text, path=anchor.path)
            if item.qualified_name == anchor.qualified_name and item.kind == anchor.kind
        ]
        if not candidates:
            short_name = anchor.qualified_name.rsplit(".", 1)[-1]
            candidates = [
                item
                for item in self.index(text, path=anchor.path)
                if item.qualified_name.rsplit(".", 1)[-1] == short_name
                and item.kind == anchor.kind
            ]
        for candidate in candidates:
            if candidate.exact_fingerprint == anchor.exact_fingerprint:
                return AnchorResolution(anchor, candidate, 1.0, "exact semantic fingerprint")
        for candidate in candidates:
            if candidate.structural_fingerprint == anchor.structural_fingerprint:
                return AnchorResolution(anchor, candidate, 0.86, "structural fingerprint")
        if candidates:
            closest = min(candidates, key=lambda item: abs(item.start_line - anchor.start_line))
            distance = abs(closest.start_line - anchor.start_line)
            confidence = max(0.25, 0.6 - min(distance, 100) / 250)
            return AnchorResolution(anchor, closest, confidence, "name and nearest line")
        return AnchorResolution(anchor, None, 0.0, "symbol not found")

    def _walk_python(
        self,
        nodes: Iterable[ast.stmt],
        output: list[SymbolAnchor],
        *,
        path: str,
        parents: tuple[str, ...],
    ) -> None:
        for node in nodes:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            kind = {
                ast.FunctionDef: "function",
                ast.AsyncFunctionDef: "async-function",
                ast.ClassDef: "class",
            }[type(node)]
            qualified = ".".join((*parents, node.name))
            exact_data = ast.dump(node, annotate_fields=True, include_attributes=False)
            structural_data = self._python_structure(node, qualified, kind)
            output.append(
                SymbolAnchor(
                    path=path,
                    qualified_name=qualified,
                    kind=kind,
                    start_line=node.lineno,
                    end_line=getattr(node, "end_lineno", node.lineno),
                    exact_fingerprint=self._hash(exact_data),
                    structural_fingerprint=self._hash(structural_data),
                )
            )
            if isinstance(node, ast.ClassDef):
                self._walk_python(node.body, output, path=path, parents=(*parents, node.name))

    @staticmethod
    def _python_structure(
        node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
        qualified: str,
        kind: str,
    ) -> str:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            all_args = (
                *node.args.posonlyargs,
                *node.args.args,
                *node.args.kwonlyargs,
            )
            args = [arg.arg for arg in all_args]
            if node.args.vararg:
                args.append("*" + node.args.vararg.arg)
            if node.args.kwarg:
                args.append("**" + node.args.kwarg.arg)
        else:
            args = [ast.dump(base, include_attributes=False) for base in node.bases]
        child_types = [type(child).__name__ for child in node.body]
        decorators = [ast.dump(item, include_attributes=False) for item in node.decorator_list]
        return repr((kind, qualified, args, decorators, child_types))

    def _generic_index(self, text: str, *, path: str) -> list[SymbolAnchor]:
        lines = text.splitlines()
        matches = list(_GENERIC_SYMBOL_RE.finditer(text))
        output = []
        for index, match in enumerate(matches):
            start_line = text.count("\n", 0, match.start()) + 1
            end_line = len(lines)
            if index + 1 < len(matches):
                end_line = text.count("\n", 0, matches[index + 1].start())
            kind = match.group("kind").replace(" ", "-")
            name = match.group("name")
            body = "\n".join(lines[start_line - 1 : end_line])
            normalized = "\n".join(
                line.strip() for line in body.splitlines() if line.strip()
            )
            child_tokens = [
                line.split()[0] for line in normalized.splitlines()[1:]
            ]
            structure = repr((kind, name, child_tokens))
            output.append(
                SymbolAnchor(
                    path,
                    name,
                    kind,
                    start_line,
                    end_line,
                    self._hash(normalized),
                    self._hash(structure),
                )
            )
        return output

    @staticmethod
    def _hash(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()
