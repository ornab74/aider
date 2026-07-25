"""High-level coordinator for Aider's bounded-context innovation layer."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from aider.innovation_actions import ActionParser, GateDecision, ToolPolicy
from aider.innovation_adaptation import SkillHotSwapController, SkillSwapDecision
from aider.innovation_anchors import SymbolAnchor, SymbolAnchorIndex
from aider.innovation_arena import PatchArenaReport, PatchCandidate, SpeculativePatchArena
from aider.innovation_budgets import AdaptiveBudgetPlanner, BudgetLedger
from aider.innovation_cache import ContextArtifactCache
from aider.innovation_capsules import (
    ContextCapsule,
    ContextLeaseBook,
    ContextProvenanceLedger,
)
from aider.innovation_causal import CausalFailureAnalyzer, CausalFailureReport
from aider.innovation_context import ContextPacket, FloatingContextManager, query_terms
from aider.innovation_contracts import (
    ContractSet,
    ContractValidation,
    InvariantContractExtractor,
    InvariantContractValidator,
)
from aider.innovation_diff import FoldedDiffReport, LargeDiffFolder
from aider.innovation_diversity import DiversityAwareReranker, DiversityReport
from aider.innovation_failures import FailureLocalizer, FailureRepairPacket
from aider.innovation_graph import CallChainPacket, HeatEntry, PythonCallGraph
from aider.innovation_large_files import (
    LargeFileDetector,
    LargeFileProfile,
    SearchFirstEditor,
    SearchWindow,
)
from aider.innovation_mcp import MCPSchemaSlimmer, SlimmedSchema
from aider.innovation_mutation import MutationPlan, MutationTestPlanner
from aider.innovation_pressure import ContextPressureMonitor, ContextPressureReport
from aider.innovation_repair import RepairOrchestrator, RepairSession
from aider.innovation_risk import BlastRadiusAnalyzer, BlastRadiusReport
from aider.innovation_routing import (
    RiskAwareModelRouter,
    RoutingDecision,
    RoutingRequest,
    RoutingStage,
)
from aider.innovation_semantic_edit import SemanticEditPlan, SemanticSedPlanner
from aider.innovation_skills import SkillMatch, SkillRegistry
from aider.innovation_tests import CounterfactualTestSelector, TestSelection
from aider.innovation_trajectory import (
    RepairTrajectoryMemory,
    TrajectoryEvent,
    TrajectorySummary,
)


@dataclass(frozen=True)
class InnovationReport:
    context: ContextPacket
    context_slice_ids: tuple[str, ...]
    skills: tuple[SkillMatch, ...]
    large_files: tuple[LargeFileProfile, ...]
    search_windows: Mapping[str, tuple[SearchWindow, ...]]
    symbol_anchors: Mapping[str, tuple[SymbolAnchor, ...]]
    actions: tuple[tuple[str, GateDecision], ...]
    risk: BlastRadiusReport
    budgets: Mapping[str, Mapping[str, float | int]]
    call_chain: CallChainPacket
    dependency_heat: tuple[HeatEntry, ...]
    folded_diff: FoldedDiffReport | None
    diversity: DiversityReport
    routing: RoutingDecision
    skill_swap: SkillSwapDecision
    contracts: tuple[ContractSet, ...]
    pressure: ContextPressureReport
    cache_hit: bool


class InnovationEngine:
    def __init__(
        self,
        workspace: str | Path,
        *,
        max_tokens: int = 4096,
        skill_roots: Iterable[str | Path] = (),
        lease_turns: int = 2,
    ) -> None:
        self.workspace = Path(workspace).resolve()
        roots = tuple(skill_roots) or (
            self.workspace / ".aider" / "skills",
            self.workspace / "skills",
            Path.home() / ".aider" / "skills",
        )
        self.context = FloatingContextManager(max_tokens=max_tokens)
        self.skills = SkillRegistry(roots)
        self.skill_swap = SkillHotSwapController(self.skills)
        self.detector = LargeFileDetector()
        self.editor = SearchFirstEditor()
        self.actions = ActionParser()
        self.policy = ToolPolicy(self.workspace)
        self.anchors = SymbolAnchorIndex()
        self.risk_analyzer = BlastRadiusAnalyzer()
        self.budget_planner = AdaptiveBudgetPlanner()
        self.leases = ContextLeaseBook(default_turns=lease_turns)
        self.provenance = ContextProvenanceLedger()
        self.failure_localizer = FailureLocalizer()
        self.test_selector = CounterfactualTestSelector()
        self.mcp_slimmer = MCPSchemaSlimmer()
        self.diff_folder = LargeDiffFolder()
        self.semantic_sed = SemanticSedPlanner()
        self.diversity = DiversityAwareReranker()
        self.router = RiskAwareModelRouter(RiskAwareModelRouter.default_profiles())
        self.cache = ContextArtifactCache()
        self.repair = RepairOrchestrator(self.router)
        self.contract_extractor = InvariantContractExtractor()
        self.contract_validator = InvariantContractValidator()
        self.patch_arena = SpeculativePatchArena(
            self.contract_validator,
            self.risk_analyzer,
        )
        self.mutation_planner = MutationTestPlanner(self.test_selector)
        self.causal_analyzer = CausalFailureAnalyzer(self.failure_localizer)
        self.pressure_monitor = ContextPressureMonitor()
        self.trajectory_memory = RepairTrajectoryMemory()
        self.last_packet: ContextPacket | None = None
        self.last_budget: BudgetLedger | None = None
        self.last_slice_ids: tuple[str, ...] = ()

    def analyze(
        self,
        files: Mapping[str, str],
        query: str,
        *,
        diff: str = "",
        total_seconds: float = 120.0,
        privacy_required: bool = False,
    ) -> InnovationReport:
        risk = self.risk_analyzer.analyze(diff)
        lookup = self.cache.get(query, files)
        if lookup.hit and lookup.packet is not None:
            packet = lookup.packet
            diversity = self.diversity.rerank(packet)
            cache_hit = True
        else:
            raw_packet = self.context.build_packet(files, query)
            diversity = self.diversity.rerank(raw_packet)
            packet = diversity.packet
            self.cache.put(query, files, packet)
            cache_hit = False

        lease_ids = self.leases.reconcile(packet)
        provenance_ids = self.provenance.record_packet(packet)
        if lease_ids != provenance_ids:
            raise RuntimeError("context lease and provenance identifiers diverged")

        pressure = self.pressure_monitor.assess(
            packet,
            previous_slice_ids=self.last_slice_ids,
            current_slice_ids=lease_ids,
            cache_hit=cache_hit,
        )
        contracts = self.contract_extractor.extract_many(files)
        skill_matches = tuple(self.skills.find(query))
        swap = self.skill_swap.choose(query, files.keys())
        profiles: list[LargeFileProfile] = []
        windows: dict[str, tuple[SearchWindow, ...]] = {}
        anchor_matches: dict[str, tuple[SymbolAnchor, ...]] = {}
        terms = query_terms(query)
        for path, text in files.items():
            profile = self.detector.profile(path, text)
            profiles.append(profile)
            if profile.is_large:
                windows[path] = tuple(self.editor.extract_windows(text, query))
            relevant_anchors = tuple(
                anchor
                for anchor in self.anchors.index(text, path=path)
                if terms & query_terms(anchor.qualified_name)
            )
            if relevant_anchors:
                anchor_matches[path] = relevant_anchors

        graph = PythonCallGraph(files)
        call_chain = graph.packet(
            query,
            max_tokens=max(500, self.context.context_budget // 2),
        )
        dependency_heat = graph.heat_map(query, limit=10)
        folded_diff = self.diff_folder.fold(diff) if diff else None
        gated_actions = tuple(
            (action.body, self.policy.evaluate(action))
            for action in self.actions.parse(query)
        )
        budget = self.budget_planner.plan(
            total_tokens=max(400, self.context.max_tokens),
            total_seconds=total_seconds,
            risk_score=risk.score,
            has_tests=any(profile.path.startswith("tests/") for profile in profiles),
        )
        routing = self.router.route(
            RoutingRequest(
                RoutingStage.EDIT if diff else RoutingStage.SEARCH,
                context_tokens=max(1, packet.token_estimate),
                output_tokens=min(2048, self.context.reserve_tokens),
                risk_score=risk.score,
                requires_tools=bool(gated_actions),
                privacy_required=privacy_required,
                preferred_tags=(swap.language.language, "code"),
            )
        )
        self.last_packet = packet
        self.last_budget = budget
        self.last_slice_ids = lease_ids
        return InnovationReport(
            context=packet,
            context_slice_ids=lease_ids,
            skills=skill_matches,
            large_files=tuple(profiles),
            search_windows=windows,
            symbol_anchors=anchor_matches,
            actions=gated_actions,
            risk=risk,
            budgets=budget.report(),
            call_chain=call_chain,
            dependency_heat=dependency_heat,
            folded_diff=folded_diff,
            diversity=diversity,
            routing=routing,
            skill_swap=swap,
            contracts=contracts,
            pressure=pressure,
            cache_hit=cache_hit,
        )

    def validate_candidate(
        self,
        baseline_files: Mapping[str, str],
        candidate_files: Mapping[str, str],
    ) -> ContractValidation:
        contracts = self.contract_extractor.extract_many(baseline_files)
        return self.contract_validator.validate(contracts, candidate_files)

    def compare_patches(
        self,
        baseline_files: Mapping[str, str],
        candidates: Sequence[PatchCandidate],
    ) -> PatchArenaReport:
        contracts = self.contract_extractor.extract_many(baseline_files)
        return self.patch_arena.evaluate(candidates, contracts)

    def analyze_failure_causes(
        self,
        output: str,
        files: Mapping[str, str],
        *,
        changed_paths: Iterable[str] = (),
    ) -> CausalFailureReport:
        return self.causal_analyzer.analyze(
            output,
            files,
            changed_paths=changed_paths,
        )

    def plan_mutations(
        self,
        files: Mapping[str, str],
        *,
        changed_paths: Iterable[str],
        changed_symbols: Iterable[str] = (),
        tests: Mapping[str, str],
    ) -> MutationPlan:
        return self.mutation_planner.plan(
            files,
            changed_paths=changed_paths,
            changed_symbols=changed_symbols,
            tests=tests,
        )

    def summarize_trajectory(
        self,
        task: str,
        events: Iterable[TrajectoryEvent],
        *,
        success: bool,
    ) -> TrajectorySummary:
        return self.trajectory_memory.summarize(task, events, success=success)

    def localize_failure(
        self, output: str, files: Mapping[str, str], *, max_tokens: int = 1600
    ) -> FailureRepairPacket:
        failure = self.failure_localizer.parse(output)
        return self.failure_localizer.build_packet(failure, files, max_tokens=max_tokens)

    def select_tests(
        self,
        *,
        changed_paths: Iterable[str],
        changed_symbols: Iterable[str] = (),
        tests: Mapping[str, str],
        failure_history: Iterable[str] = (),
    ) -> TestSelection:
        return self.test_selector.select(
            changed_paths=changed_paths,
            changed_symbols=changed_symbols,
            tests=tests,
            failure_history=failure_history,
        )

    def slim_mcp(self, schema: Mapping[str, object], query: str) -> SlimmedSchema:
        return self.mcp_slimmer.slim(schema, query)

    def plan_symbol_edit(
        self, path: str | Path, text: str, symbol: str, replacement: str
    ) -> SemanticEditPlan:
        return self.semantic_sed.plan(path, text, symbol, replacement)

    def new_repair_session(
        self,
        task: str,
        risk_score: int,
        *,
        token_limit: int = 8000,
        tests_available: bool = True,
    ) -> RepairSession:
        return RepairSession(
            task,
            risk_score,
            token_limit=token_limit,
            tests_available=tests_available,
        )

    def finish_turn(self, *, used_slice_ids: Iterable[str] = ()) -> tuple[str, ...]:
        for identifier in tuple(used_slice_ids):
            self.leases.mark_used(identifier)
            self.provenance.record_use(identifier)
        return self.leases.advance()

    def checkpoint(self, path: str | Path) -> ContextCapsule:
        if self.last_packet is None:
            raise RuntimeError("analyze must run before checkpoint")
        capsule = ContextCapsule.capture(
            self.last_packet,
            self.context.state,
            self.leases,
            self.provenance,
        )
        capsule.save(path)
        return capsule
