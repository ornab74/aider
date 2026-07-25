"""Bounded Python call-chain packets and dependency heat maps."""

from __future__ import annotations

import ast
import math
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from aider.innovation_context import estimate_tokens, query_terms


@dataclass(frozen=True)
class SymbolNode:
    identifier: str
    path: str
    qualified_name: str
    start_line: int
    end_line: int
    source: str

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
        ]
        for node in self.nodes:
            lines.append(
                f"\n## {node.identifier} lines {node.start_line}-{node.end_line}\n"
                f"{node.source.rstrip()}"
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


class PythonCallGraph:
    def __init__(self, files: Mapping[str, str]) -> None:
        self.files = {str(Path(path)): text for path, text in files.items()}
        self.nodes: dict[str, SymbolNode] = {}
        self.edges: dict[str, set[str]] = defaultdict(set)
        self.reverse: dict[str, set[str]] = defaultdict(set)
        self._build()

    def _build(self) -> None:
        pending: dict[str, tuple[SymbolNode, tuple[str, ...]]] = {}
        for path, text in self.files.items():
            if not path.endswith(".py"):
                continue
            try:
                tree = ast.parse(text)
            except SyntaxError:
                continue
            module = path[:-3].replace("/", ".").replace("\\", ".")
            lines = text.splitlines()
            imports: dict[str, str] = {}
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        imports[alias.asname or alias.name.split(".")[0]] = alias.name
                elif isinstance(node, ast.ImportFrom):
                    for alias in node.names:
                        imports[alias.asname or alias.name] = (
                            f"{node.module or ''}.{alias.name}".strip(".")
                        )

            def visit(body: list[ast.stmt], parents: tuple[str, ...] = ()) -> None:
                for item in body:
                    if isinstance(item, ast.ClassDef):
                        visit(item.body, (*parents, item.name))
                    elif isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        local = ".".join((*parents, item.name))
                        qualified = f"{module}.{local}"
                        start = item.lineno
                        end = getattr(item, "end_lineno", start)
                        source = "\n".join(
                            f"{number:>6} | {line}"
                            for number, line in enumerate(lines[start - 1 : end], start=start)
                        )
                        identifier = f"{path}:{qualified}"
                        calls = tuple(
                            sorted(
                                {
                                    self._call_name(call.func, imports)
                                    for call in ast.walk(item)
                                    if isinstance(call, ast.Call)
                                    and self._call_name(call.func, imports)
                                }
                            )
                        )
                        pending[identifier] = (
                            SymbolNode(identifier, path, qualified, start, end, source),
                            calls,
                        )
                        visit(item.body, (*parents, item.name))

            visit(tree.body)

        self.nodes = {identifier: value[0] for identifier, value in pending.items()}
        by_name = {node.qualified_name: identifier for identifier, node in self.nodes.items()}
        by_short: dict[str, list[str]] = defaultdict(list)
        for identifier, node in self.nodes.items():
            by_short[node.qualified_name.rsplit(".", 1)[-1]].append(identifier)
        for caller, (node, calls) in pending.items():
            for raw in calls:
                target = by_name.get(raw)
                if target is None:
                    options = by_short.get(raw.rsplit(".", 1)[-1], [])
                    target = options[0] if len(options) == 1 else None
                if target and target != caller:
                    self.edges[caller].add(target)
                    self.reverse[target].add(caller)

    @staticmethod
    def _call_name(node: ast.AST, imports: Mapping[str, str]) -> str:
        if isinstance(node, ast.Name):
            return imports.get(node.id, node.id)
        if isinstance(node, ast.Attribute):
            parent = PythonCallGraph._call_name(node.value, imports)
            return f"{parent}.{node.attr}" if parent else node.attr
        return ""

    def packet(
        self, query: str, *, depth: int = 1, max_nodes: int = 12, max_tokens: int = 2200
    ) -> CallChainPacket:
        terms = query_terms(query)
        ranked = sorted(self.nodes.values(), key=lambda node: (-self._relevance(node, terms), node.identifier))
        seeds = [node.identifier for node in ranked if self._relevance(node, terms) > 0][:3]
        if not seeds and ranked:
            seeds = [ranked[0].identifier]
        distances: dict[str, int] = {}
        queue = deque((seed, 0) for seed in seeds)
        while queue:
            identifier, distance = queue.popleft()
            if identifier in distances and distances[identifier] <= distance:
                continue
            distances[identifier] = distance
            if distance < depth:
                for neighbor in sorted(self.edges[identifier] | self.reverse[identifier]):
                    queue.append((neighbor, distance + 1))
        candidates = sorted(
            (self.nodes[item] for item in distances),
            key=lambda node: (distances[node.identifier], -self._relevance(node, terms)),
        )
        chosen = []
        consumed = estimate_tokens(query) + 48
        for node in candidates:
            cost = node.token_estimate + 18
            if len(chosen) < max_nodes and consumed + cost <= max_tokens:
                chosen.append(node)
                consumed += cost
        chosen_ids = {node.identifier for node in chosen}
        edges = tuple(
            CallEdge(caller, callee)
            for caller in sorted(chosen_ids)
            for callee in sorted(self.edges[caller])
            if callee in chosen_ids
        )
        return CallChainPacket(
            query,
            tuple(seed for seed in seeds if seed in chosen_ids),
            tuple(chosen),
            edges,
            consumed,
            max_tokens,
            max(0, len(candidates) - len(chosen)),
        )

    def heat_map(
        self, query: str = "", *, touches: Mapping[str, int] | None = None, limit: int = 12
    ) -> tuple[HeatEntry, ...]:
        terms = query_terms(query)
        touches = touches or {}
        output = []
        for identifier, node in self.nodes.items():
            callers = len(self.reverse[identifier])
            callees = len(self.edges[identifier])
            touched = int(touches.get(node.path, 0))
            relevance = self._relevance(node, terms)
            score = relevance + math.log2(2 + callers * 2 + callees) * 3 + min(20, touched * 2)
            reasons = []
            if relevance:
                reasons.append("query match")
            if callers or callees:
                reasons.append("dependency centrality")
            if touched:
                reasons.append("recently touched")
            output.append(HeatEntry(identifier, node.path, score, callers, callees, touched, tuple(reasons)))
        return tuple(sorted(output, key=lambda item: (-item.score, item.identifier))[:limit])

    @staticmethod
    def _relevance(node: SymbolNode, terms: set[str]) -> float:
        name_terms = query_terms(node.qualified_name)
        body = node.source.lower()
        return len(terms & name_terms) * 12.0 + sum(body.count(term) for term in terms) * 0.5
