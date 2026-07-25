"""Static invariant extraction and contract validation for speculative edits."""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Mapping

_INVARIANT_RE = re.compile(r"^\s*#\s*invariant\s*:\s*(?P<value>.+)$", re.I | re.M)


class ContractSeverity(str, Enum):
    ADVISORY = "advisory"
    BLOCKING = "blocking"


@dataclass(frozen=True)
class ContractRule:
    identifier: str
    kind: str
    value: str
    severity: ContractSeverity = ContractSeverity.BLOCKING


@dataclass(frozen=True)
class ContractSet:
    path: str
    language: str
    rules: tuple[ContractRule, ...]


@dataclass(frozen=True)
class ContractViolation:
    rule: ContractRule
    message: str


@dataclass(frozen=True)
class ContractValidation:
    valid: bool
    violations: tuple[ContractViolation, ...]
    checked_rules: int

    @property
    def blocking_count(self) -> int:
        return sum(
            item.rule.severity == ContractSeverity.BLOCKING for item in self.violations
        )


class InvariantContractExtractor:
    """Capture compatibility promises that a surgical patch should preserve."""

    def extract(self, path: str, text: str) -> ContractSet:
        if path.endswith(".py"):
            return self._extract_python(path, text)
        rules = [
            ContractRule(
                f"comment:{index}",
                "literal",
                value.strip(),
                ContractSeverity.ADVISORY,
            )
            for index, value in enumerate(_INVARIANT_RE.findall(text), start=1)
        ]
        return ContractSet(path, "generic", tuple(rules))

    def extract_many(self, files: Mapping[str, str]) -> tuple[ContractSet, ...]:
        return tuple(self.extract(path, text) for path, text in sorted(files.items()))

    def _extract_python(self, path: str, text: str) -> ContractSet:
        rules: list[ContractRule] = []
        try:
            tree = ast.parse(text)
        except SyntaxError:
            return ContractSet(
                path,
                "python",
                (
                    ContractRule(
                        "python:syntax",
                        "syntax",
                        "valid Python syntax",
                    ),
                ),
            )
        rules.append(ContractRule("python:syntax", "syntax", "valid Python syntax"))
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if node.name.startswith("_"):
                    continue
                kind = "class" if isinstance(node, ast.ClassDef) else "function"
                rules.append(
                    ContractRule(
                        f"public:{node.name}",
                        "public-symbol",
                        f"{kind}:{node.name}",
                    )
                )
                if not isinstance(node, ast.ClassDef):
                    rules.append(
                        ContractRule(
                            f"signature:{node.name}",
                            "signature",
                            self._signature(node),
                        )
                    )
        for index, value in enumerate(_INVARIANT_RE.findall(text), start=1):
            rules.append(
                ContractRule(
                    f"comment:{index}",
                    "literal",
                    value.strip(),
                    ContractSeverity.ADVISORY,
                )
            )
        return ContractSet(path, "python", tuple(rules))

    @staticmethod
    def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
        args = node.args
        names = [item.arg for item in args.posonlyargs + args.args]
        if args.vararg:
            names.append("*" + args.vararg.arg)
        names.extend(item.arg for item in args.kwonlyargs)
        if args.kwarg:
            names.append("**" + args.kwarg.arg)
        prefix = "async " if isinstance(node, ast.AsyncFunctionDef) else ""
        return f"{prefix}{node.name}({','.join(names)})"


class InvariantContractValidator:
    def validate(
        self,
        contracts: Iterable[ContractSet],
        candidate_files: Mapping[str, str],
    ) -> ContractValidation:
        violations: list[ContractViolation] = []
        checked = 0
        for contract in contracts:
            candidate = candidate_files.get(contract.path)
            if candidate is None:
                for rule in contract.rules:
                    checked += 1
                    violations.append(
                        ContractViolation(rule, f"required file {contract.path} is missing")
                    )
                continue
            facts = self._candidate_facts(contract.language, candidate)
            for rule in contract.rules:
                checked += 1
                if not self._satisfied(rule, candidate, facts):
                    violations.append(
                        ContractViolation(
                            rule,
                            f"{contract.path} no longer satisfies {rule.kind}: {rule.value}",
                        )
                    )
        blocking = any(
            item.rule.severity == ContractSeverity.BLOCKING for item in violations
        )
        return ContractValidation(not blocking, tuple(violations), checked)

    def _candidate_facts(self, language: str, text: str) -> dict[str, object]:
        if language != "python":
            return {"syntax": True, "symbols": set(), "signatures": set()}
        try:
            tree = ast.parse(text)
        except SyntaxError:
            return {"syntax": False, "symbols": set(), "signatures": set()}
        symbols = set()
        signatures = set()
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                kind = "class" if isinstance(node, ast.ClassDef) else "function"
                symbols.add(f"{kind}:{node.name}")
                if not isinstance(node, ast.ClassDef):
                    signatures.add(InvariantContractExtractor._signature(node))
        return {"syntax": True, "symbols": symbols, "signatures": signatures}

    @staticmethod
    def _satisfied(rule: ContractRule, text: str, facts: Mapping[str, object]) -> bool:
        if rule.kind == "syntax":
            return bool(facts["syntax"])
        if rule.kind == "public-symbol":
            return rule.value in facts["symbols"]
        if rule.kind == "signature":
            return rule.value in facts["signatures"]
        if rule.kind == "literal":
            return rule.value in text
        return False
