"""Bracket action protocol and explicit safety gates for tool execution."""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import Mapping, Sequence

from aider.innovation_capabilities import CapabilityTokenBroker, CapabilityTokenError

_ACTION_RE = re.compile(
    r"\[action:(?P<tool>[\w.-]+)(?P<attrs>[^\]]*)\](?P<body>.*?)\[/action\]",
    re.DOTALL | re.IGNORECASE,
)
_ATTR_RE = re.compile(r"([\w.-]+)=(?:\"([^\"]*)\"|'([^']*)'|([^\s]+))")


class RiskLevel(IntEnum):
    READ_ONLY = 0
    WORKSPACE_WRITE = 1
    NETWORK = 2
    DESTRUCTIVE = 3
    SECRET_ACCESS = 4


@dataclass(frozen=True)
class ActionRequest:
    tool: str
    body: str
    attributes: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class GateDecision:
    allowed: bool
    risk: RiskLevel
    requires_approval: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class ExecutionResult:
    returncode: int
    stdout: str
    stderr: str
    decision: GateDecision


class ActionParser:
    def parse(self, text: str) -> list[ActionRequest]:
        actions: list[ActionRequest] = []
        for match in _ACTION_RE.finditer(text):
            attrs: dict[str, str] = {}
            for attr in _ATTR_RE.finditer(match.group("attrs")):
                attrs[attr.group(1)] = next(
                    value for value in attr.groups()[1:] if value is not None
                )
            actions.append(
                ActionRequest(
                    tool=match.group("tool").lower(),
                    body=match.group("body").strip(),
                    attributes=attrs,
                )
            )
        return actions


class ToolPolicy:
    READ_ONLY_COMMANDS = {
        "cat",
        "cut",
        "diff",
        "find",
        "git",
        "grep",
        "head",
        "ls",
        "pwd",
        "rg",
        "sed",
        "stat",
        "tail",
        "wc",
    }
    WRITE_COMMANDS = {"cp", "mkdir", "mv", "python", "python3", "touch"}
    NETWORK_COMMANDS = {"curl", "git", "npm", "pip", "wget"}
    DESTRUCTIVE_COMMANDS = {"dd", "mkfs", "rm", "shred"}
    SECRET_PATTERNS = (
        re.compile(r"(?:^|/)(?:\.ssh|\.aws|\.gnupg)(?:/|$)"),
        re.compile(r"(?:^|/)(?:\.env|credentials|secrets?)(?:\.|/|$)", re.IGNORECASE),
    )

    def __init__(
        self,
        workspace: str | Path,
        *,
        allow_network: bool = False,
        allow_workspace_writes: bool = False,
    ) -> None:
        self.workspace = Path(workspace).resolve()
        self.allow_network = allow_network
        self.allow_workspace_writes = allow_workspace_writes

    def evaluate(self, action: ActionRequest) -> GateDecision:
        reasons: list[str] = []
        risk = RiskLevel.READ_ONLY
        if action.tool not in {"terminal", "powershell", "python", "mcp"}:
            return GateDecision(False, RiskLevel.DESTRUCTIVE, True, ("unknown tool",))

        if action.tool == "mcp":
            risk = RiskLevel.NETWORK
            reasons.append("MCP tools can cross process or network boundaries")
        elif action.tool == "python":
            risk = RiskLevel.WORKSPACE_WRITE
            reasons.append("arbitrary Python can mutate the workspace")
        else:
            try:
                argv = shlex.split(action.body, posix=action.tool != "powershell")
            except ValueError as exc:
                return GateDecision(False, RiskLevel.DESTRUCTIVE, True, (f"parse error: {exc}",))
            if not argv:
                return GateDecision(False, RiskLevel.READ_ONLY, False, ("empty command",))
            executable = Path(argv[0]).name.lower()
            combined = " ".join(argv)
            if executable in self.DESTRUCTIVE_COMMANDS:
                risk = RiskLevel.DESTRUCTIVE
                reasons.append(f"destructive executable: {executable}")
            elif executable in self.NETWORK_COMMANDS and self._looks_networked(argv):
                risk = RiskLevel.NETWORK
                reasons.append(f"network-capable executable: {executable}")
            elif executable in self.WRITE_COMMANDS or self._contains_write_operator(action.body):
                risk = RiskLevel.WORKSPACE_WRITE
                reasons.append("command may write files")
            elif executable not in self.READ_ONLY_COMMANDS:
                risk = RiskLevel.WORKSPACE_WRITE
                reasons.append("command is not on the read-only allowlist")
            if any(pattern.search(combined) for pattern in self.SECRET_PATTERNS):
                risk = RiskLevel.SECRET_ACCESS
                reasons.append("command references a sensitive path")

        if risk == RiskLevel.SECRET_ACCESS or risk == RiskLevel.DESTRUCTIVE:
            return GateDecision(False, risk, True, tuple(reasons))
        if risk == RiskLevel.NETWORK:
            return GateDecision(self.allow_network, risk, True, tuple(reasons))
        if risk == RiskLevel.WORKSPACE_WRITE:
            return GateDecision(self.allow_workspace_writes, risk, True, tuple(reasons))
        return GateDecision(True, risk, False, tuple(reasons or ["read-only allowlist"]))

    @staticmethod
    def _contains_write_operator(command: str) -> bool:
        return bool(re.search(r"(?:^|\s)(?:>|>>|2>|&>)", command))

    @staticmethod
    def _looks_networked(argv: Sequence[str]) -> bool:
        if Path(argv[0]).name.lower() == "git":
            return any(
                part in {"clone", "fetch", "pull", "push", "submodule"}
                for part in argv[1:]
            )
        return True


class GatedSandbox:
    """Execute approved actions inside a workspace boundary.

    This is a process gate, not a kernel sandbox. For hard isolation, use the
    generated container command with an OS-level container runtime.
    """

    def __init__(
        self,
        policy: ToolPolicy,
        *,
        timeout: int = 30,
        token_broker: CapabilityTokenBroker | None = None,
    ) -> None:
        self.policy = policy
        self.timeout = timeout
        self.token_broker = token_broker or CapabilityTokenBroker()

    def issue_capability(self, action: ActionRequest) -> str:
        decision = self.policy.evaluate(action)
        if not decision.allowed:
            raise PermissionError("cannot grant a capability for a policy-blocked action")
        if not decision.requires_approval:
            raise ValueError("read-only actions do not require capability tokens")
        return self.token_broker.issue(action, int(decision.risk))

    def run(
        self,
        action: ActionRequest,
        *,
        approved: bool = False,
        capability_token: str | None = None,
    ) -> ExecutionResult:
        decision = self.policy.evaluate(action)
        if not decision.allowed:
            return ExecutionResult(126, "", "action blocked by policy", decision)
        if decision.requires_approval and not approved:
            if capability_token is None:
                return ExecutionResult(125, "", "action requires approval", decision)
            try:
                self.token_broker.consume(capability_token, action, int(decision.risk))
            except CapabilityTokenError as exc:
                return ExecutionResult(125, "", str(exc), decision)
        if action.tool not in {"terminal", "powershell"}:
            return ExecutionResult(
                126, "", "direct execution is limited to shell actions", decision
            )

        cwd = self._resolve_cwd(action.attributes.get("cwd", "."))
        argv = shlex.split(action.body, posix=action.tool != "powershell")
        env_keys = {"PATH", "HOME", "LANG", "TERM"}
        env = {key: value for key, value in os.environ.items() if key in env_keys}
        completed = subprocess.run(
            argv,
            cwd=cwd,
            env=env,
            text=True,
            capture_output=True,
            timeout=self.timeout,
            check=False,
        )
        return ExecutionResult(
            completed.returncode,
            completed.stdout,
            completed.stderr,
            decision,
        )

    def container_command(
        self, action: ActionRequest, *, image: str = "python:3.12-slim"
    ) -> str:
        workspace = shlex.quote(str(self.policy.workspace))
        body = shlex.quote(action.body)
        return (
            f"docker run --rm --network none --read-only --cap-drop ALL "
            f"--security-opt no-new-privileges -v {workspace}:/workspace:rw "
            f"-w /workspace {shlex.quote(image)} sh -lc {body}"
        )

    def manifest(self, action: ActionRequest) -> str:
        decision = self.policy.evaluate(action)
        return json.dumps(
            {
                "tool": action.tool,
                "body": action.body,
                "attributes": dict(action.attributes),
                "risk": decision.risk.name,
                "allowed": decision.allowed,
                "requires_approval": decision.requires_approval,
                "reasons": decision.reasons,
            },
            indent=2,
        )

    def _resolve_cwd(self, requested: str) -> Path:
        candidate = (self.policy.workspace / requested).resolve()
        if candidate != self.policy.workspace and self.policy.workspace not in candidate.parents:
            raise ValueError("cwd escapes the configured workspace")
        return candidate
