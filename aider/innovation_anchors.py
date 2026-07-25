"""Formatting-resilient symbol fingerprints for verified surgical edits."""

from __future__ import annotations

import ast
import hashlib
import re
from dataclasses import dataclass

_GENERIC_SYMBOL_RE = re.compile(
    r"^\s*(?P<kind>async\s+def|def|class|function|interface|enum|struct|fn)\s+"
    r"(?P<name>[A-Za-z_$][\w$]*)",
    re.MULTILINE,
)


@dataclass(frozen=True)
class SymbolAnchor:
    path: str
    qualified_name: str
    kind: str
    start_line: int
    end_line: int
    structural_hash: str


@dataclass(frozen=True)
class AnchorResolution:
    requested: SymbolAnchor
    matched: SymbolAnchor | None
    confidence: float
    reason: str


class SymbolAnchorIndex:
    def index(self, text: str, *, path: str = "") -> list[SymbolAnchor]:
        if path.endswith(".py") or self._looks_python(text):
            try:
                tree = ast.parse(text)
            except SyntaxError:
                return self._generic_index(text, path=path)
            output: list[SymbolAnchor] = []
            self._walk_python(tree.body, output, path=path, parents=())
            return output
        return self._generic_index(text, path=path)

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
        candidates = self.index(text, path=anchor.path)
        same_name = [item for item in candidates if item.qualified_name == anchor.qualified_name]
        same_hash = [item for item in candidates if item.structural_hash == anchor.structural_hash]
        if same_name and same_name[0].structural_hash == anchor.structural_hash:
            return AnchorResolution(anchor, same_name[0], 1.0, "name and structure match")
        if len(same_hash) == 1:
            return AnchorResolution(anchor, same_hash[0], 0.92, "structure moved or renamed")
        if len(same_name) == 1:
            return AnchorResolution(anchor, same_name[0], 0.65, "name matches but structure changed")
        return AnchorResolution(anchor, None, 0.0, "anchor could not be resolved uniquely")

    def _walk_python(
        self,
        nodes: list[ast.stmt],
        output: list[SymbolAnchor],
        *,
        path: str,
        parents: tuple[str, ...],
    ) -> None:
        for node in nodes:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            qualified = ".".join((*parents, node.name))
            kind = "class" if isinstance(node, ast.ClassDef) else "async-def" if isinstance(node, ast.AsyncFunctionDef) else "def"
            structure = self._python_structure(node, qualified, kind)
            output.append(
                SymbolAnchor(
                    path,
                    qualified,
                    kind,
                    node.lineno,
                    getattr(node, "end_lineno", node.lineno),
                    hashlib.sha256(structure.encode()).hexdigest(),
                )
            )
            self._walk_python(node.body, output, path=path, parents=(*parents, node.name))

    @staticmethod
    def _python_structure(node: ast.AST, qualified: str, kind: str) -> str:
        normalized = ast.dump(node, annotate_fields=True, include_attributes=False)
        return repr((kind, qualified, normalized))

    def _generic_index(self, text: str, *, path: str) -> list[SymbolAnchor]:
        lines = text.splitlines()
        matches = list(_GENERIC_SYMBOL_RE.finditer(text))
        output = []
        for index, match in enumerate(matches):
            start = text.count("\n", 0, match.start()) + 1
            end = len(lines)
            if index + 1 < len(matches):
                end = text.count("\n", 0, matches[index + 1].start())
            body = "\n".join(lines[start - 1 : end])
            normalized = "\n".join(line.strip() for line in body.splitlines() if line.strip())
            output.append(
                SymbolAnchor(
                    path,
                    match.group("name"),
                    match.group("kind").replace(" ", "-"),
                    start,
                    end,
                    hashlib.sha256(normalized.encode()).hexdigest(),
                )
            )
        return output

    @staticmethod
    def _looks_python(text: str) -> bool:
        return bool(re.search(r"^\s*(?:from\s+\S+\s+import|import\s+\S+|def\s+|class\s+)", text, re.M))
