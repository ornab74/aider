"""Mutation-guided verification planning without executing untrusted mutations."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

from aider.innovation_tests import CounterfactualTestSelector, TestSelection


@dataclass(frozen=True)
class MutationTarget:
    path: str
    symbol: str
    line: int
    operator: str
    replacement: str
    rationale: str


@dataclass(frozen=True)
class MutationPlan:
    targets: tuple[MutationTarget, ...]
    selected_tests: TestSelection
    omitted_targets: int


class MutationTestPlanner:
    """Propose small semantic mutations that selected tests should detect."""

    _COMPARE_FLIPS = {
        ast.Eq: "!=",
        ast.NotEq: "==",
        ast.Lt: ">=",
        ast.LtE: ">",
        ast.Gt: "<=",
        ast.GtE: "<",
        ast.Is: "is not",
        ast.IsNot: "is",
        ast.In: "not in",
        ast.NotIn: "in",
    }

    def __init__(self, selector: CounterfactualTestSelector | None = None) -> None:
        self.selector = selector or CounterfactualTestSelector()

    def plan(
        self,
        files: Mapping[str, str],
        *,
        changed_paths: Iterable[str],
        changed_symbols: Iterable[str] = (),
        tests: Mapping[str, str],
        max_targets: int = 24,
        max_tests: int = 8,
    ) -> MutationPlan:
        changed = {str(Path(path)) for path in changed_paths}
        symbols = {item.rsplit(".", 1)[-1] for item in changed_symbols}
        targets: list[MutationTarget] = []
        for raw_path, text in sorted(files.items()):
            path = str(Path(raw_path))
            if path not in changed or not path.endswith(".py"):
                continue
            try:
                tree = ast.parse(text)
            except SyntaxError:
                continue
            visitor = _MutationVisitor(path, symbols)
            visitor.visit(tree)
            targets.extend(visitor.targets)
        targets.sort(key=lambda item: (item.path, item.line, item.operator, item.symbol))
        selected = self.selector.select(
            changed_paths=changed,
            changed_symbols=symbols,
            tests=tests,
            limit=max_tests,
        )
        return MutationPlan(
            tuple(targets[:max_targets]),
            selected,
            max(0, len(targets) - max_targets),
        )


class _MutationVisitor(ast.NodeVisitor):
    def __init__(self, path: str, selected_symbols: set[str]) -> None:
        self.path = path
        self.selected_symbols = selected_symbols
        self.stack: list[str] = []
        self.targets: list[MutationTarget] = []

    @property
    def symbol(self) -> str:
        return ".".join(self.stack) or "<module>"

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_scope(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_scope(node)

    def _visit_scope(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.stack.append(node.name)
        if not self.selected_symbols or node.name in self.selected_symbols:
            self.generic_visit(node)
        self.stack.pop()

    def visit_Compare(self, node: ast.Compare) -> None:
        for operator in node.ops:
            replacement = MutationTestPlanner._COMPARE_FLIPS.get(type(operator))
            if replacement:
                self.targets.append(
                    MutationTarget(
                        self.path,
                        self.symbol,
                        node.lineno,
                        type(operator).__name__,
                        replacement,
                        "flip a branch comparison to verify boundary coverage",
                    )
                )
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, bool):
            replacement = repr(not node.value)
            rationale = "invert a boolean decision"
        elif isinstance(node.value, int) and not isinstance(node.value, bool):
            replacement = str(node.value + 1)
            rationale = "shift an integer boundary by one"
        else:
            return
        self.targets.append(
            MutationTarget(
                self.path,
                self.symbol,
                node.lineno,
                type(node.value).__name__,
                replacement,
                rationale,
            )
        )
