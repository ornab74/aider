"""Risk-aware model routing without coupling to any specific provider."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable


class RoutingStage(str, Enum):
    SEARCH = "search"
    EDIT = "edit"
    TEST = "test"
    REPAIR = "repair"
    VERIFY = "verify"


@dataclass(frozen=True)
class ModelProfile:
    name: str
    context_window: int
    max_output_tokens: int
    quality: float
    tool_reliability: float
    speed: float
    local: bool = False
    supports_tools: bool = True
    tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.context_window < 1 or self.max_output_tokens < 1:
            raise ValueError("model token capacities must be positive")
        for field_name in ("quality", "tool_reliability", "speed"):
            value = getattr(self, field_name)
            if not 0 <= value <= 1:
                raise ValueError(f"{field_name} must be between 0 and 1")


@dataclass(frozen=True)
class RoutingRequest:
    stage: RoutingStage
    context_tokens: int
    output_tokens: int = 1024
    risk_score: int = 0
    requires_tools: bool = False
    retries: int = 0
    privacy_required: bool = False
    preferred_tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class RoutingDecision:
    primary: ModelProfile
    verifier: ModelProfile | None
    score: float
    reasons: tuple[str, ...]
    escalation_trigger: str | None


class RiskAwareModelRouter:
    def __init__(self, profiles: Iterable[ModelProfile]) -> None:
        self.profiles = tuple(profiles)
        if not self.profiles:
            raise ValueError("at least one model profile is required")

    def route(self, request: RoutingRequest) -> RoutingDecision:
        eligible = [profile for profile in self.profiles if self._eligible(profile, request)]
        if not eligible:
            raise ValueError(
                "no model can satisfy the requested context, output, and tool constraints"
            )
        scored = sorted(
            ((self._score(profile, request), profile) for profile in eligible),
            key=lambda item: (-item[0], item[1].name),
        )
        score, primary = scored[0]
        reasons = self._reasons(primary, request)
        verifier = self._verifier(primary, request, eligible)
        trigger = None
        if request.retries >= 2:
            trigger = "repeated repair failure"
        elif request.risk_score >= 70:
            trigger = "critical blast radius"
        elif request.risk_score >= 45:
            trigger = "high-risk patch requires independent verification"
        return RoutingDecision(primary, verifier, score, tuple(reasons), trigger)

    @staticmethod
    def default_profiles() -> tuple[ModelProfile, ...]:
        return (
            ModelProfile(
                "local-fast",
                16_384,
                2_048,
                quality=0.55,
                tool_reliability=0.62,
                speed=0.95,
                local=True,
                tags=("search", "summarize"),
            ),
            ModelProfile(
                "local-code",
                32_768,
                4_096,
                quality=0.72,
                tool_reliability=0.76,
                speed=0.72,
                local=True,
                tags=("code", "edit", "repair"),
            ),
            ModelProfile(
                "specialist-code",
                131_072,
                8_192,
                quality=0.92,
                tool_reliability=0.90,
                speed=0.42,
                tags=("code", "verify", "architecture"),
            ),
        )

    @staticmethod
    def _eligible(profile: ModelProfile, request: RoutingRequest) -> bool:
        if request.context_tokens + request.output_tokens > profile.context_window:
            return False
        if request.output_tokens > profile.max_output_tokens:
            return False
        if request.requires_tools and not profile.supports_tools:
            return False
        if request.privacy_required and not profile.local:
            return False
        return True

    @staticmethod
    def _score(profile: ModelProfile, request: RoutingRequest) -> float:
        risk = min(100, max(0, request.risk_score)) / 100.0
        stage_quality = {
            RoutingStage.SEARCH: profile.speed * 0.45 + profile.quality * 0.25,
            RoutingStage.EDIT: profile.quality * 0.52 + profile.tool_reliability * 0.25,
            RoutingStage.TEST: profile.tool_reliability * 0.48 + profile.speed * 0.20,
            RoutingStage.REPAIR: profile.quality * 0.55 + profile.tool_reliability * 0.30,
            RoutingStage.VERIFY: profile.quality * 0.72 + profile.tool_reliability * 0.18,
        }[request.stage]
        reliability = profile.tool_reliability * (0.15 + risk * 0.25)
        quality = profile.quality * risk * 0.35
        local_bonus = (
            0.18
            if request.privacy_required and profile.local
            else 0.04 if profile.local else 0.0
        )
        tag_bonus = 0.03 * len(set(profile.tags) & set(request.preferred_tags))
        retry_bonus = request.retries * profile.quality * 0.04
        capacity_headroom = max(
            0.0,
            1.0 - (request.context_tokens + request.output_tokens) / profile.context_window,
        )
        return (
            stage_quality
            + reliability
            + quality
            + local_bonus
            + tag_bonus
            + retry_bonus
            + capacity_headroom * 0.05
        )

    def _verifier(
        self,
        primary: ModelProfile,
        request: RoutingRequest,
        eligible: list[ModelProfile],
    ) -> ModelProfile | None:
        if request.risk_score < 45 and request.retries < 2:
            return None
        alternatives = [profile for profile in eligible if profile.name != primary.name]
        if not alternatives:
            return None
        verify_request = RoutingRequest(
            RoutingStage.VERIFY,
            request.context_tokens,
            request.output_tokens,
            max(60, request.risk_score),
            request.requires_tools,
            request.retries,
            request.privacy_required,
            ("verify",),
        )
        return max(alternatives, key=lambda profile: self._score(profile, verify_request))

    @staticmethod
    def _reasons(profile: ModelProfile, request: RoutingRequest) -> list[str]:
        reasons = [f"selected for {request.stage.value} stage"]
        if profile.local:
            reasons.append("local execution")
        if request.privacy_required:
            reasons.append("privacy constraint satisfied")
        if request.requires_tools:
            reasons.append("tool-capable profile")
        if request.risk_score >= 45:
            reasons.append("quality and reliability weighted for patch risk")
        if request.retries:
            reasons.append(f"{request.retries} prior retries increased quality preference")
        return reasons
