"""Symbol call-chain packets and dependency heat maps for bounded code context."""

from __future__ import annotations

import ast
import math
from collections import Counter, defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

from aider.innovation_context import estimate_tokens, query_terms


@dataclass(frozen=True)
class SymbolNode:
    identifier: str
    path: str
    module: str
    qualified_name: str
    kind: str
    start_line: int
    end_line: int
    source: str
    calls: tuple[str, ...]

    @property
    def token_estimate(self) -> int:
        return estimate_tokens(self.source)


@dataclass(frozen=True)
class CallEdge:
    caller: str
    callee: str


@dataclass(frozen=True)
class CallChainPacket:
    query: str
    seeds: tuple[str, ...]
    nodes: tuple[SymbolNode, ...]
    edges: tuple[CallEdge, ...]
    token_estimate: int
    budget: int
    omitted_nodes: int = 0

    def render(self) -> str:
        lines = [
            "# Symbol call-chain packet",
            f"# query: {self.query}",
            f"# estimated tokens: {self.token_estimate}/{self.budget}",
            f"# seeds: {', '.join(self.seeds) or 'none'}",
        ]
        for node in self.nodes:
            lines.append(
                f"\n## {node.identifier} [{node.kind}] "
                f"lines {node.start_line}-{node.end_line}\n{node.source.rstrip()}"
            )
        if self.edges:
            lines.append("\n## Call edges")
            lines.extend(f"- {edge.caller} -> {edge.callee}" for edge in self.edges)
        return "\n".join(lines) + "\n"


@dataclass(frozen=True)
class HeatEntry:
    identifier: str
    path: str
    score: float
    callers: int
    callees: int
    touches: int
    reasons: tuple[str, ...]


class _DefinitionCollector(ast.NodeVisitor):
    def __init__(self, path: str, text: str) -> None:
        self.path = path
        self.text = text
        self.lines = text.splitlines()
        self.module = _module_name(path)
        self.stack: list[str] = []
        self.imports: dict[str, str] = {}
        self.nodes: list[SymbolNode] = []

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.imports[alias.asname or alias.name.split(".")[0]] = alias.name

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        for alias in node.names:
            full = f"{module}.{alias.name}".strip(".")
            self.imports[alias.asname or alias.name] = full

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._record_function(node, "function")

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._record_function(node, "async_function")

    def _record_function(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef, kind: str
    ) -> None:
        local_name = ".".join((*self.stack, node.name))
        qualified = f"{self.module}.{local_name}" if self.module else local_name
        start = node.lineno
        end = getattr(node, "end_lineno", node.lineno)
        source = "\n".join(
            f"{line_no:>6} | {line}"
            for line_no, line in enumerate(self.lines[start - 1 : end], start=start)
        )
        calls = tuple(sorted(set(_extract_calls(node, self.imports, qualified))))
        self.nodes.append(
            SymbolNode(
                identifier=f"{self.path}:{qualified}",
                path=self.path,
                module=self.module,
                qualified_name=qualified,
                kind=kind,
                start_line=start,
                end_line=end,
                source=source,
                calls=calls,
            )
        )
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()


class PythonCallGraph:
    def __init__(self, files: Mapping[str, str]) -> None:
        self.files = {str(Path(path)): text for path, text in files.items()}
        self.nodes: dict[str, SymbolNode] = {}
        self.edges: dict[str, set[str]] = defaultdict(set)
        self.reverse_edges: dict[str, set[str]] = defaultdict(set)
        self._build()

    def _build(self) -> None:
        collected: list[SymbolNode] = []
        for path, text in self.files.items():
            if not path.endswith(".py"):
                continue
            try:
                tree = ast.parse(text)
            except SyntaxError:
                continue
            collector = _DefinitionCollector(path, text)
            collector.visit(tree)
            collected.extend(collector.nodes)
        self.nodes = {node.identifier: node for node in collected}
        by_qualified = {node.qualified_name: node.identifier for node in collected}
        by_short: dict[str, list[str]] = defaultdict(list)
        for node in collected:
            by_short[node.qualified_name.rsplit(".", 1)[-1]].append(node.identifier)

        for node in collected:
            for raw_call in node.calls:
                target = self._resolve_call(node, raw_call, by_qualified, by_short)
                if target is None or target == node.identifier:
                    continue
                self.edges[node.identifier].add(target)
                self.reverse_edges[target].add(node.identifier)

    @staticmethod
    def _resolve_call(
        caller: SymbolNode,
        raw_call: str,
        by_qualified: Mapping[str, str],
        by_short: Mapping[str, list[str]],
    ) -> str | None:
        if raw_call in by_qualified:
            return by_qualified[raw_call]
        if raw_call.startswith("self."):
            parent = caller.qualified_name.rsplit(".", 1)[0]
            candidate = f"{parent}.{raw_call.split('.', 1)[1]}"
            if candidate in by_qualified:
                return by_qualified[candidate]
        if "." not in raw_call:
            module_candidate = f"{caller.module}.{raw_call}" if caller.module else raw_call
            if module_candidate in by_qualified:
                return by_qualified[module_candidate]
            parent = caller.qualified_name.rsplit(".", 1)[0]
            class_candidate = f"{parent}.{raw_call}"
            if class_candidate in by_qualified:
                return by_qualified[class_candidate]
            options = by_short.get(raw_call, [])
            if len(options) == 1:
                return options[0]
        suffix = f".{raw_call}"
        options = [
            identifier
            for name, identifier in by_qualified.items()
            if name.endswith(suffix)
        ]
        if len(options) == 1:
            return options[0]
        return None

    def packet(
        self,
        query: str,
        *,
        depth: int = 1,
        max_nodes: int = 12,
        max_tokens: int = 2200,
    ) -> CallChainPacket:
        if depth < 0:
            raise ValueError("depth cannot be negative")
        terms = query_terms(query)
        ranked = sorted(
            self.nodes.values(),
            key=lambda node: (-self._relevance(node, terms), node.identifier),
        )
        seeds = [node.identifier for node in ranked if self._relevance(node, terms) > 0][:3]
        if not seeds and ranked:
            seeds = [ranked[0].identifier]

        distances: dict[str, int] = {}
        queue: deque[tuple[str, int]] = deque((seed, 0) for seed in seeds)
        while queue:
            identifier, distance = queue.popleft()
            if identifier in distances and distances[identifier] <= distance:
                continue
            distances[identifier] = distance
            if distance >= depth:
                continue
            neighbors = (
                self.edges.get(identifier, set())
                | self.reverse_edges.get(identifier, set())
            )
            for neighbor in sorted(neighbors):
                queue.append((neighbor, distance + 1))

        candidates = sorted(
            (self.nodes[identifier] for identifier in distances),
            key=lambda node: (
                distances[node.identifier],
                -self._relevance(node, terms),
                node.identifier,
            ),
        )
        chosen: list[SymbolNode] = []
        consumed = estimate_tokens(query) + 48
        for node in candidates:
            cost = node.token_estimate + 18
            if len(chosen) >= max_nodes or consumed + cost > max_tokens:
                continue
            chosen.append(node)
            consumed += cost
        chosen_ids = {node.identifier for node in chosen}
        edges = tuple(
            CallEdge(caller, callee)
            for caller in sorted(chosen_ids)
            for callee in sorted(self.edges.get(caller, set()))
            if callee in chosen_ids
        )
        return CallChainPacket(
            query=query,
            seeds=tuple(seed for seed in seeds if seed in chosen_ids),
            nodes=tuple(chosen),
            edges=edges,
            token_estimate=consumed,
            budget=max_tokens,
            omitted_nodes=max(0, len(candidates) - len(chosen)),
        )

    def heat_map(
        self,
        query: str = "",
        *,
        touches: Mapping[str, int] | None = None,
        limit: int = 12,
    ) -> tuple[HeatEntry, ...]:
        terms = query_terms(query)
        touches = touches or {}
        entries = []
        for identifier, node in self.nodes.items():
            callers = len(self.reverse_edges.get(identifier, set()))
            callees = len(self.edges.get(identifier, set()))
            touch_count = int(touches.get(node.path, 0))
            relevance = self._relevance(node, terms)
            centrality = math.log2(2 + callers * 2 + callees)
            score = relevance + centrality * 3.0 + min(20, touch_count * 2)
            reasons = []
            if relevance:
                reasons.append("query relevance")
            if callers:
                reasons.append(f"{callers} callers")
            if callees:
                reasons.append(f"{callees} callees")
            if touch_count:
                reasons.append(f"{touch_count} touches")
            entries.append(
                HeatEntry(
                    identifier,
                    node.path,
                    round(score, 3),
                    callers,
                    callees,
                    touch_count,
                    tuple(reasons),
                )
            )
        return tuple(sorted(entries, key=lambda item: (-item.score, item.identifier))[:limit])

    @staticmethod
    def _relevance(node: SymbolNode, terms: set[str]) -> float:
        if not terms:
            return 1.0
        symbol_terms = query_terms(node.qualified_name)
        path_terms = query_terms(node.path)
        source_lower = node.source.lower()
        exact = len(terms & symbol_terms)
        path = len(terms & path_terms)
        mentions = sum(source_lower.count(term) for term in terms)
        return exact * 12.0 + path * 5.0 + min(18.0, mentions * 2.0)


def _module_name(path: str) -> str:
    value = str(Path(path).with_suffix("")).replace("\\", "/")
    parts = [part for part in value.split("/") if part not in {".", ""}]
    return ".".join(parts)


def _extract_calls(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    imports: Mapping[str, str],
    qualified_name: str,
) -> Iterable[str]:
    del qualified_name

    class CallVisitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.calls: list[str] = []

        def visit_Call(self, child: ast.Call) -> None:
            name = _call_name(child.func)
            if name:
                first, dot, remainder = name.partition(".")
                if first in imports:
                    name = imports[first] + (dot + remainder if dot else "")
                self.calls.append(name)
            self.generic_visit(child)

        def visit_FunctionDef(self, child: ast.FunctionDef) -> None:
            if child is node:
                self.generic_visit(child)

        def visit_AsyncFunctionDef(self, child: ast.AsyncFunctionDef) -> None:
            if child is node:
                self.generic_visit(child)

        def visit_Lambda(self, child: ast.Lambda) -> None:
            return

    visitor = CallVisitor()
    visitor.visit(node)
    yield from visitor.calls


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _call_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""
