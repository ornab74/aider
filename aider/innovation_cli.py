"""CLI entry point for Aider's experimental context-surgery layer."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from aider.innovation_actions import ActionParser, GatedSandbox, ToolPolicy
from aider.innovation_adaptation import (
    SkillDraftAuthor,
    SkillHotSwapController,
    WorkflowMemory,
)
from aider.innovation_anchors import SymbolAnchorIndex
from aider.innovation_arena import PatchCandidate, SpeculativePatchArena
from aider.innovation_budgets import AdaptiveBudgetPlanner
from aider.innovation_cache import ContextArtifactCache
from aider.innovation_causal import CausalFailureAnalyzer
from aider.innovation_capsules import (
    ContextCapsule,
    ContextLeaseBook,
    ContextProvenanceLedger,
)
from aider.innovation_context import FloatingContextManager
from aider.innovation_contracts import (
    InvariantContractExtractor,
    InvariantContractValidator,
)
from aider.innovation_diff import LargeDiffFolder, invert_unified_diff
from aider.innovation_diversity import DiversityAwareReranker
from aider.innovation_failures import FailureLocalizer
from aider.innovation_graph import PythonCallGraph
from aider.innovation_large_files import LargeFileDetector, SearchFirstEditor
from aider.innovation_mcp import MCPSchemaSlimmer
from aider.innovation_mutation import MutationTestPlanner
from aider.innovation_pressure import ContextPressureMonitor
from aider.innovation_repair import RepairOrchestrator, RepairSession
from aider.innovation_risk import BlastRadiusAnalyzer
from aider.innovation_routing import (
    ModelProfile,
    RiskAwareModelRouter,
    RoutingRequest,
    RoutingStage,
)
from aider.innovation_semantic_edit import SemanticSedPlanner
from aider.innovation_skills import SkillRegistry
from aider.innovation_tests import CounterfactualTestSelector
from aider.innovation_trajectory import (
    RepairTrajectoryMemory,
    TrajectoryEvent,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aider-innovate")
    subparsers = parser.add_subparsers(dest="command", required=True)

    context = subparsers.add_parser("context", help="build a query-focused context packet")
    context.add_argument("query")
    context.add_argument("files", nargs="+")
    context.add_argument("--max-tokens", type=int, default=4096)
    context.add_argument("--capsule-out")

    inspect = subparsers.add_parser("large-file", help="profile and search a large file")
    inspect.add_argument("file")
    inspect.add_argument("query")
    inspect.add_argument("--context-lines", type=int, default=24)

    skills = subparsers.add_parser("skill", help="find relevant SKILL.md files")
    skills.add_argument("query")
    skills.add_argument("--root", action="append", default=[])
    skills.add_argument("--limit", type=int, default=5)

    action = subparsers.add_parser("action", help="gate [action:*] requests")
    action.add_argument("request")
    action.add_argument("--workspace", default=".")
    action.add_argument("--approve", action="store_true")
    action.add_argument("--allow-network", action="store_true")
    action.add_argument("--allow-write", action="store_true")
    action.add_argument("--execute", action="store_true")

    anchor = subparsers.add_parser("anchor", help="fingerprint and resolve a source symbol")
    anchor.add_argument("file")
    anchor.add_argument("symbol")

    risk = subparsers.add_parser("risk", help="score the blast radius of a unified diff")
    risk.add_argument("diff_file", help="path to a diff, or - for stdin")

    budget = subparsers.add_parser("budget", help="plan stage-specific token/time budgets")
    budget.add_argument("--tokens", type=int, default=4096)
    budget.add_argument("--seconds", type=float, default=120.0)
    budget.add_argument("--risk-score", type=int, default=0)
    budget.add_argument("--no-tests", action="store_true")

    verify = subparsers.add_parser("verify-capsule", help="verify a saved context capsule")
    verify.add_argument("capsule")
    verify.add_argument("--root", default=".")

    chain = subparsers.add_parser("call-chain", help="build a bounded symbol call chain")
    chain.add_argument("query")
    chain.add_argument("files", nargs="+")
    chain.add_argument("--depth", type=int, default=1)
    chain.add_argument("--max-tokens", type=int, default=2200)
    chain.add_argument("--heat", action="store_true")

    failure = subparsers.add_parser("failure", help="localize a pytest failure")
    failure.add_argument("output_file", help="pytest output path, or - for stdin")
    failure.add_argument("files", nargs="+")
    failure.add_argument("--max-tokens", type=int, default=1600)

    select = subparsers.add_parser(
        "select-tests", help="rank tests that distinguish a proposed edit"
    )
    select.add_argument("tests", nargs="+")
    select.add_argument("--changed", action="append", required=True)
    select.add_argument("--symbol", action="append", default=[])
    select.add_argument("--limit", type=int, default=8)

    slim = subparsers.add_parser("slim-mcp", help="reduce an MCP tool schema for a task")
    slim.add_argument("schema_file")
    slim.add_argument("query")
    slim.add_argument("--max-tools", type=int, default=6)
    slim.add_argument("--max-properties", type=int, default=10)

    fold = subparsers.add_parser("fold-diff", help="summarize repeated large-diff edits")
    fold.add_argument("diff_file", help="path to a diff, or - for stdin")
    fold.add_argument("--invert", action="store_true")

    semantic = subparsers.add_parser(
        "semantic-sed", help="create a verified SED plan for one symbol"
    )
    semantic.add_argument("file")
    semantic.add_argument("symbol")
    semantic.add_argument("replacement_file")

    diverse = subparsers.add_parser(
        "diversify-context", help="rerank a context packet for file and lexical diversity"
    )
    diverse.add_argument("query")
    diverse.add_argument("files", nargs="+")
    diverse.add_argument("--max-tokens", type=int, default=4096)

    route = subparsers.add_parser("route-model", help="select a model profile for a stage")
    route.add_argument("--profiles")
    route.add_argument("--stage", choices=[item.value for item in RoutingStage], default="edit")
    route.add_argument("--context-tokens", type=int, default=2000)
    route.add_argument("--output-tokens", type=int, default=1024)
    route.add_argument("--risk-score", type=int, default=0)
    route.add_argument("--requires-tools", action="store_true")
    route.add_argument("--retries", type=int, default=0)
    route.add_argument("--privacy", action="store_true")
    route.add_argument("--tag", action="append", default=[])

    swap = subparsers.add_parser("skill-swap", help="select or hot-swap the active skill")
    swap.add_argument("query")
    swap.add_argument("files", nargs="*")
    swap.add_argument("--root", action="append", default=[])

    draft = subparsers.add_parser("draft-skill", help="draft a SKILL.md from successful history")
    draft.add_argument("history")
    draft.add_argument("name")
    draft.add_argument("language")
    draft.add_argument("--minimum-successes", type=int, default=3)
    draft.add_argument("--write-root")
    draft.add_argument("--allow-write", action="store_true")

    cache = subparsers.add_parser("cache-key", help="compute a content-addressed context key")
    cache.add_argument("query")
    cache.add_argument("files", nargs="+")

    repair = subparsers.add_parser("repair-plan", help="advance a resumable repair session")
    repair.add_argument("session")
    repair.add_argument(
        "event", choices=["start", "edit", "test-pass", "test-fail", "quorum", "resume"]
    )
    repair.add_argument("--task", default="repair task")
    repair.add_argument("--risk-score", type=int, default=0)
    repair.add_argument("--token-limit", type=int, default=8000)
    repair.add_argument("--tokens", type=int, default=0)
    repair.add_argument("--output-file")
    repair.add_argument("--no-tests", action="store_true")
    repair.add_argument("--save", action="store_true")

    contracts = subparsers.add_parser(
        "check-contracts", help="validate candidate files against baseline invariants"
    )
    contracts.add_argument("spec", help="JSON with baseline and candidate file maps")

    arena = subparsers.add_parser(
        "patch-arena", help="rank speculative patches before applying any write"
    )
    arena.add_argument("spec", help="JSON with baseline and candidate patches")

    mutations = subparsers.add_parser(
        "mutation-plan", help="plan semantic mutations and targeted tests"
    )
    mutations.add_argument("sources", nargs="+")
    mutations.add_argument("--test", action="append", required=True)
    mutations.add_argument("--changed", action="append", required=True)
    mutations.add_argument("--symbol", action="append", default=[])

    causal = subparsers.add_parser(
        "causal-failure", help="rank likely causes from a failure and call graph"
    )
    causal.add_argument("output_file", help="pytest output path, or - for stdin")
    causal.add_argument("files", nargs="+")
    causal.add_argument("--changed", action="append", default=[])

    pressure = subparsers.add_parser(
        "context-pressure", help="measure prompt saturation, redundancy, and churn"
    )
    pressure.add_argument("query")
    pressure.add_argument("files", nargs="+")
    pressure.add_argument("--max-tokens", type=int, default=4096)

    trajectory = subparsers.add_parser(
        "trajectory", help="compress or recall repair trajectories"
    )
    trajectory.add_argument("memory")
    trajectory.add_argument("operation", choices=["record", "recall"])
    trajectory.add_argument("task")
    trajectory.add_argument("--events")
    trajectory.add_argument("--success", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "context":
        files = _read_files(args.files)
        manager = FloatingContextManager(max_tokens=args.max_tokens)
        packet = manager.build_packet(files, args.query)
        print(packet.render())
        if args.capsule_out:
            leases = ContextLeaseBook()
            leases.reconcile(packet)
            provenance = ContextProvenanceLedger()
            provenance.record_packet(packet)
            ContextCapsule.capture(packet, manager.state, leases, provenance).save(
                args.capsule_out
            )
        return 0

    if args.command == "large-file":
        path = Path(args.file)
        text = path.read_text(encoding="utf-8", errors="replace")
        detector = LargeFileDetector()
        editor = SearchFirstEditor()
        profile = detector.profile(path, text)
        print(json.dumps(asdict(profile), indent=2))
        plan = editor.build_search_plan(path, args.query)
        print(f"\nsearch: {plan.ripgrep_command}")
        for window in editor.extract_windows(
            text, args.query, context_lines=args.context_lines
        ):
            print(f"\n## lines {window.start_line}-{window.end_line}\n{window.text}")
        return 0

    if args.command == "skill":
        roots = args.root or [
            "skills",
            ".aider/skills",
            str(Path.home() / ".aider/skills"),
        ]
        matches = SkillRegistry(roots).find(args.query, limit=args.limit)
        for match in matches:
            print(f"\n=== {match.skill.name} ({match.score:.2f}) ===\n{match.summary}")
        return 0 if matches else 1

    if args.command == "action":
        actions = ActionParser().parse(args.request)
        if not actions:
            print("No [action:tool]...[/action] block found", file=sys.stderr)
            return 2
        policy = ToolPolicy(
            args.workspace,
            allow_network=args.allow_network,
            allow_workspace_writes=args.allow_write,
        )
        sandbox = GatedSandbox(policy)
        exit_code = 0
        for action in actions:
            print(sandbox.manifest(action))
            if args.execute:
                result = sandbox.run(action, approved=args.approve)
                if result.stdout:
                    print(result.stdout, end="")
                if result.stderr:
                    print(result.stderr, file=sys.stderr)
                exit_code = max(exit_code, result.returncode)
        return exit_code

    if args.command == "anchor":
        path = Path(args.file)
        text = path.read_text(encoding="utf-8", errors="replace")
        anchor = SymbolAnchorIndex().create(text, args.symbol, path=str(path))
        print(json.dumps(asdict(anchor), indent=2))
        return 0

    if args.command == "risk":
        diff = _read_stream_or_file(args.diff_file)
        report = BlastRadiusAnalyzer().analyze(diff)
        print(json.dumps(asdict(report), indent=2))
        return 0

    if args.command == "budget":
        ledger = AdaptiveBudgetPlanner().plan(
            total_tokens=args.tokens,
            total_seconds=args.seconds,
            risk_score=args.risk_score,
            has_tests=not args.no_tests,
        )
        print(json.dumps(ledger.report(), indent=2))
        return 0

    if args.command == "verify-capsule":
        capsule = ContextCapsule.load(args.capsule)
        root = Path(args.root)
        files = {}
        for item in capsule.packet.slices:
            path = root / item.path
            if path.exists():
                files[item.path] = path.read_text(encoding="utf-8", errors="replace")
        result = capsule.verify(files)
        print(json.dumps(asdict(result), indent=2))
        return 0 if result.valid else 1

    if args.command == "call-chain":
        graph = PythonCallGraph(_read_files(args.files))
        packet = graph.packet(
            args.query,
            depth=args.depth,
            max_tokens=args.max_tokens,
        )
        print(packet.render())
        if args.heat:
            print("# Dependency heat map")
            for item in graph.heat_map(args.query):
                print(f"{item.score:7.2f}  {item.identifier}  {', '.join(item.reasons)}")
        return 0 if packet.nodes else 1

    if args.command == "failure":
        output = _read_stream_or_file(args.output_file)
        localizer = FailureLocalizer()
        packet = localizer.build_packet(
            localizer.parse(output),
            _read_files(args.files),
            max_tokens=args.max_tokens,
        )
        print(packet.render())
        return 0 if packet.slices else 1

    if args.command == "select-tests":
        selection = CounterfactualTestSelector().select(
            changed_paths=args.changed,
            changed_symbols=args.symbol,
            tests=_read_files(args.tests),
            limit=args.limit,
        )
        print(json.dumps([asdict(item) for item in selection.candidates], indent=2))
        print(f"\n{selection.command}")
        return 0 if selection.candidates else 1

    if args.command == "slim-mcp":
        schema = json.loads(Path(args.schema_file).read_text(encoding="utf-8"))
        result = MCPSchemaSlimmer().slim(
            schema,
            args.query,
            max_tools=args.max_tools,
            max_properties=args.max_properties,
        )
        print(json.dumps(result.schema, indent=2))
        print(
            f"\n# tools={len(result.selected_tools)} "
            f"tokens={result.slim_token_estimate}/{result.original_token_estimate} "
            f"reduction={result.reduction_ratio:.1%}",
            file=sys.stderr,
        )
        return 0

    if args.command == "fold-diff":
        diff = _read_stream_or_file(args.diff_file)
        print(LargeDiffFolder().fold(diff).render())
        if args.invert:
            print("\n# Inverted patch\n")
            print(invert_unified_diff(diff), end="")
        return 0

    if args.command == "semantic-sed":
        path = Path(args.file)
        text = path.read_text(encoding="utf-8", errors="replace")
        replacement = Path(args.replacement_file).read_text(encoding="utf-8")
        plan = SemanticSedPlanner().plan(path, text, args.symbol, replacement)
        print(json.dumps(asdict(plan.resolution), indent=2))
        print("\n" + plan.sed_script)
        return 0

    if args.command == "diversify-context":
        files = _read_files(args.files)
        manager = FloatingContextManager(max_tokens=args.max_tokens)
        report = DiversityAwareReranker().rerank(manager.build_packet(files, args.query))
        print(report.packet.render())
        print(
            json.dumps(
                {
                    "file_entropy": report.file_entropy,
                    "distinct_files": report.distinct_files,
                    "covered_terms": report.covered_terms,
                    "omitted_for_similarity": report.omitted_for_similarity,
                },
                indent=2,
            ),
            file=sys.stderr,
        )
        return 0

    if args.command == "route-model":
        if args.profiles:
            payload = json.loads(Path(args.profiles).read_text(encoding="utf-8"))
            profiles = tuple(ModelProfile(**item) for item in payload)
        else:
            profiles = RiskAwareModelRouter.default_profiles()
        decision = RiskAwareModelRouter(profiles).route(
            RoutingRequest(
                RoutingStage(args.stage),
                args.context_tokens,
                args.output_tokens,
                args.risk_score,
                args.requires_tools,
                args.retries,
                args.privacy,
                tuple(args.tag),
            )
        )
        print(json.dumps(asdict(decision), indent=2))
        return 0

    if args.command == "skill-swap":
        roots = args.root or ["skills", ".aider/skills", str(Path.home() / ".aider/skills")]
        decision = SkillHotSwapController(SkillRegistry(roots)).choose(args.query, args.files)
        print(json.dumps(asdict(decision), indent=2, default=str))
        return 0 if decision.active_skill else 1

    if args.command == "draft-skill":
        memory = WorkflowMemory.load(args.history)
        draft = SkillDraftAuthor(minimum_successes=args.minimum_successes).draft(
            args.name, args.language, memory.records
        )
        print(draft.content)
        if args.write_root:
            output = SkillDraftAuthor(
                minimum_successes=args.minimum_successes
            ).write(draft, args.write_root, allow_write=args.allow_write)
            print(f"# path: {output}", file=sys.stderr)
        return 0 if draft.ready else 1

    if args.command == "cache-key":
        files = _read_files(args.files)
        print(ContextArtifactCache().key_for(args.query, files))
        return 0


    if args.command == "check-contracts":
        payload = json.loads(Path(args.spec).read_text(encoding="utf-8"))
        baseline = {str(key): str(value) for key, value in payload["baseline"].items()}
        candidate = {str(key): str(value) for key, value in payload["candidate"].items()}
        extractor = InvariantContractExtractor()
        result = InvariantContractValidator().validate(
            extractor.extract_many(baseline), candidate
        )
        print(json.dumps(asdict(result), indent=2))
        return 0 if result.valid else 1

    if args.command == "patch-arena":
        payload = json.loads(Path(args.spec).read_text(encoding="utf-8"))
        baseline = {str(key): str(value) for key, value in payload["baseline"].items()}
        contracts = InvariantContractExtractor().extract_many(baseline)
        candidates = [
            PatchCandidate(
                item["name"],
                {str(key): str(value) for key, value in item["files"].items()},
                item.get("diff", ""),
                tuple(item.get("tests_selected", ())),
                tuple(item.get("notes", ())),
            )
            for item in payload["candidates"]
        ]
        report = SpeculativePatchArena().evaluate(candidates, contracts)
        print(json.dumps(asdict(report), indent=2))
        return 0 if report.winner is not None else 1

    if args.command == "mutation-plan":
        plan = MutationTestPlanner().plan(
            _read_files(args.sources),
            changed_paths=args.changed,
            changed_symbols=args.symbol,
            tests=_read_files(args.test),
        )
        print(json.dumps(asdict(plan), indent=2))
        return 0 if plan.targets else 1

    if args.command == "causal-failure":
        report = CausalFailureAnalyzer().analyze(
            _read_stream_or_file(args.output_file),
            _read_files(args.files),
            changed_paths=args.changed,
        )
        print(json.dumps(asdict(report), indent=2))
        return 0 if report.hypotheses else 1

    if args.command == "context-pressure":
        manager = FloatingContextManager(max_tokens=args.max_tokens)
        packet = manager.build_packet(_read_files(args.files), args.query)
        report = ContextPressureMonitor().assess(packet)
        print(json.dumps(asdict(report), indent=2))
        return 0

    if args.command == "trajectory":
        memory_path = Path(args.memory)
        memory = (
            RepairTrajectoryMemory.load(memory_path)
            if memory_path.exists()
            else RepairTrajectoryMemory()
        )
        if args.operation == "recall":
            print(json.dumps([asdict(item) for item in memory.recall(args.task)], indent=2))
            return 0
        if not args.events:
            print("--events is required for record", file=sys.stderr)
            return 2
        payload = json.loads(Path(args.events).read_text(encoding="utf-8"))
        events = [TrajectoryEvent(**item) for item in payload]
        summary = memory.summarize(args.task, events, success=args.success)
        memory.save(memory_path)
        print(json.dumps(asdict(summary), indent=2))
        return 0

    if args.command == "repair-plan":
        path = Path(args.session)
        if path.exists():
            session = RepairOrchestrator.load(path)
        else:
            session = RepairSession(
                args.task,
                args.risk_score,
                token_limit=args.token_limit,
                tests_available=not args.no_tests,
            )
        orchestrator = RepairOrchestrator(
            RiskAwareModelRouter(RiskAwareModelRouter.default_profiles())
        )
        if args.event == "start":
            decision = orchestrator.start(session)
        elif args.event == "edit":
            decision = orchestrator.record_edit(session, tokens=args.tokens)
        elif args.event == "test-pass":
            decision = orchestrator.record_test(session, passed=True, tokens=args.tokens)
        elif args.event == "test-fail":
            output = _read_stream_or_file(args.output_file) if args.output_file else "test failed"
            decision = orchestrator.record_test(
                session, passed=False, output=output, tokens=args.tokens
            )
        elif args.event == "quorum":
            decision = orchestrator.approve_quorum(session)
        else:
            decision = orchestrator.resume(session)
        print(json.dumps({"session": asdict(session), "decision": asdict(decision)}, indent=2))
        if args.save:
            orchestrator.save(session, path)
        return 0

    return 2


def _read_files(paths: list[str]) -> dict[str, str]:
    return {
        str(Path(path)): Path(path).read_text(encoding="utf-8", errors="replace")
        for path in paths
    }


def _read_stream_or_file(path: str) -> str:
    return sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
