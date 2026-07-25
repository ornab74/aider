"""Resumable, budget-aware repair orchestration and escalation policy."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path

from aider.innovation_routing import (
    RiskAwareModelRouter,
    RoutingDecision,
    RoutingRequest,
    RoutingStage,
)


class RepairPhase(str, Enum):
    ANALYZE = "analyze"
    SEARCH = "search"
    EDIT = "edit"
    TEST = "test"
    REPAIR = "repair"
    VERIFY = "verify"
    ESCALATE = "escalate"
    PAUSED = "paused"
    COMPLETE = "complete"


@dataclass
class RepairSession:
    task: str
    risk_score: int
    phase: RepairPhase = RepairPhase.ANALYZE
    attempts: int = 0
    failure_fingerprints: list[str] = field(default_factory=list)
    consumed_tokens: int = 0
    token_limit: int = 8_000
    tests_available: bool = True
    patch_quorum_reached: bool = False
    checkpoint: str | None = None

    @property
    def remaining_tokens(self) -> int:
        return max(0, self.token_limit - self.consumed_tokens)


@dataclass(frozen=True)
class RepairDecision:
    phase: RepairPhase
    next_action: str
    routing: RoutingDecision | None
    checkpoint_required: bool
    reasons: tuple[str, ...]


class RepairOrchestrator:
    def __init__(self, router: RiskAwareModelRouter) -> None:
        self.router = router

    def start(self, session: RepairSession, *, context_tokens: int = 1200) -> RepairDecision:
        if session.remaining_tokens <= 0:
            return self._pause(session, "token budget exhausted before analysis")
        session.phase = RepairPhase.SEARCH
        routing = self.router.route(
            RoutingRequest(
                RoutingStage.SEARCH,
                context_tokens,
                risk_score=session.risk_score,
                privacy_required=False,
                preferred_tags=("search",),
            )
        )
        return RepairDecision(
            session.phase,
            "build bounded context, call-chain, and failure packets",
            routing,
            False,
            ("session initialized",),
        )

    def record_edit(
        self,
        session: RepairSession,
        *,
        tokens: int,
        context_tokens: int = 1800,
    ) -> RepairDecision:
        self._consume(session, tokens)
        if session.remaining_tokens <= 0:
            return self._pause(session, "edit consumed the remaining token budget")
        session.phase = RepairPhase.TEST if session.tests_available else RepairPhase.VERIFY
        stage = RoutingStage.TEST if session.tests_available else RoutingStage.VERIFY
        return RepairDecision(
            session.phase,
            (
                "run counterfactually selected tests"
                if session.tests_available
                else "verify diff and invariants"
            ),
            self.router.route(
                RoutingRequest(
                    stage,
                    context_tokens,
                    risk_score=session.risk_score,
                    requires_tools=session.tests_available,
                    retries=session.attempts,
                    preferred_tags=(stage.value,),
                )
            ),
            session.risk_score >= 45,
            ("edit recorded",),
        )

    def record_test(
        self,
        session: RepairSession,
        *,
        passed: bool,
        output: str = "",
        tokens: int = 0,
        context_tokens: int = 1600,
    ) -> RepairDecision:
        self._consume(session, tokens)
        if passed:
            if session.risk_score >= 45 and not session.patch_quorum_reached:
                session.phase = RepairPhase.VERIFY
                return RepairDecision(
                    session.phase,
                    "obtain independent patch quorum",
                    self.router.route(
                        RoutingRequest(
                            RoutingStage.VERIFY,
                            context_tokens,
                            risk_score=session.risk_score,
                            retries=session.attempts,
                            preferred_tags=("verify",),
                        )
                    ),
                    True,
                    ("tests passed", "risk requires independent verification"),
                )
            session.phase = RepairPhase.COMPLETE
            return RepairDecision(
                session.phase,
                "finalize patch and provenance record",
                None,
                False,
                ("tests and policy checks passed",),
            )

        session.attempts += 1
        fingerprint = self.failure_fingerprint(output)
        repeated = fingerprint in session.failure_fingerprints
        session.failure_fingerprints.append(fingerprint)
        if session.remaining_tokens <= 0:
            return self._pause(session, "test failure exhausted the token budget")
        if repeated or session.attempts >= 3:
            session.phase = RepairPhase.ESCALATE
            return RepairDecision(
                session.phase,
                "checkpoint and escalate with the localized failure packet",
                self.router.route(
                    RoutingRequest(
                        RoutingStage.REPAIR,
                        context_tokens,
                        risk_score=max(60, session.risk_score),
                        retries=session.attempts,
                        preferred_tags=("repair", "architecture"),
                    )
                ),
                True,
                (
                    (
                        "failure repeated without progress"
                        if repeated
                        else "repair retry limit reached"
                    ),
                ),
            )
        session.phase = RepairPhase.REPAIR
        return RepairDecision(
            session.phase,
            "localize the new failure and perform one bounded repair",
            self.router.route(
                RoutingRequest(
                    RoutingStage.REPAIR,
                    context_tokens,
                    risk_score=session.risk_score,
                    retries=session.attempts,
                    preferred_tags=("repair",),
                )
            ),
            False,
            ("new failure fingerprint",),
        )

    def approve_quorum(self, session: RepairSession) -> RepairDecision:
        session.patch_quorum_reached = True
        session.phase = RepairPhase.COMPLETE
        return RepairDecision(
            session.phase,
            "finalize verified patch",
            None,
            False,
            ("independent patch quorum reached",),
        )

    def resume(self, session: RepairSession) -> RepairDecision:
        if session.phase != RepairPhase.PAUSED:
            raise ValueError("only paused sessions can be resumed")
        if session.remaining_tokens <= 0:
            return self._pause(session, "increase or reallocate budget before resuming")
        session.phase = RepairPhase.REPAIR if session.attempts else RepairPhase.SEARCH
        return RepairDecision(
            session.phase,
            "resume from checkpoint with only active leases and failure evidence",
            self.router.route(
                RoutingRequest(
                    RoutingStage.REPAIR if session.attempts else RoutingStage.SEARCH,
                    min(1600, session.remaining_tokens),
                    risk_score=session.risk_score,
                    retries=session.attempts,
                )
            ),
            False,
            ("paused session resumed",),
        )

    @staticmethod
    def save(session: RepairSession, path: str | Path) -> None:
        payload = asdict(session)
        payload["phase"] = session.phase.value
        Path(path).write_text(json.dumps(payload, indent=2), encoding="utf-8")

    @staticmethod
    def load(path: str | Path) -> RepairSession:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        payload["phase"] = RepairPhase(payload["phase"])
        return RepairSession(**payload)

    @staticmethod
    def failure_fingerprint(output: str) -> str:
        normalized = "\n".join(
            line.strip()
            for line in output.splitlines()
            if line.strip() and not line.strip().startswith("File ")
        )
        return hashlib.sha256(normalized.encode()).hexdigest()[:20]

    @staticmethod
    def _consume(session: RepairSession, tokens: int) -> None:
        if tokens < 0:
            raise ValueError("token consumption cannot be negative")
        session.consumed_tokens += tokens

    @staticmethod
    def _pause(session: RepairSession, reason: str) -> RepairDecision:
        session.phase = RepairPhase.PAUSED
        return RepairDecision(
            session.phase,
            "save a resumable checkpoint",
            None,
            True,
            (reason,),
        )
