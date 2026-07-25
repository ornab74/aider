import json
from pathlib import Path

from aider.innovation_adaptation import (
    SkillDraftAuthor,
    SkillHotSwapController,
    WorkflowMemory,
    WorkflowRecord,
)
from aider.innovation_cache import ContextArtifactCache
from aider.innovation_context import ContextPacket, ContextSlice
from aider.innovation_diversity import DiversityAwareReranker, DiversityPolicy
from aider.innovation_engine import InnovationEngine
from aider.innovation_repair import RepairOrchestrator, RepairPhase, RepairSession
from aider.innovation_routing import (
    RiskAwareModelRouter,
    RoutingRequest,
    RoutingStage,
)
from aider.innovation_skills import SkillRegistry


def _packet() -> ContextPacket:
    return ContextPacket(
        "retry auth",
        (
            ContextSlice(
                "auth.py",
                1,
                20,
                "def retry_auth():\n    return token\n" * 4,
                20.0,
                ("query hits",),
            ),
            ContextSlice(
                "auth.py",
                21,
                40,
                "def retry_auth_again():\n    return token\n" * 4,
                19.5,
                ("query hits",),
            ),
            ContextSlice(
                "transport.py",
                1,
                20,
                "def send_retry():\n    return retry_auth()\n" * 4,
                16.0,
                ("symbol match",),
            ),
        ),
        180,
        700,
    )


def test_diversity_reranker_spreads_context_across_files():
    report = DiversityAwareReranker(
        DiversityPolicy(max_slices_per_file=1)
    ).rerank(_packet())
    assert [item.path for item in report.packet.slices] == [
        "auth.py",
        "transport.py",
    ]
    assert report.distinct_files == 2
    assert report.file_entropy == 1.0
    assert report.omitted_for_similarity == 1


def test_risk_router_prefers_local_for_privacy_and_verifier_for_high_risk():
    router = RiskAwareModelRouter(RiskAwareModelRouter.default_profiles())
    private = router.route(
        RoutingRequest(
            RoutingStage.SEARCH,
            context_tokens=1000,
            risk_score=5,
            privacy_required=True,
        )
    )
    assert private.primary.local
    assert private.verifier is None

    risky = router.route(
        RoutingRequest(
            RoutingStage.EDIT,
            context_tokens=2000,
            risk_score=80,
            requires_tools=True,
        )
    )
    assert risky.primary.name == "specialist-code"
    assert risky.verifier is not None
    assert risky.verifier.name != risky.primary.name


def test_skill_hot_swap_tracks_language_change(tmp_path: Path):
    for name, description, tags in (
        ("python", "Python testing workflow", "[python, pytest]"),
        ("dart", "Dart and Flutter workflow", "[dart, flutter]"),
    ):
        folder = tmp_path / name
        folder.mkdir()
        (folder / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {description}\ntags: {tags}\n---\n"
            f"# Workflow\nUse the {name} workflow.\n",
            encoding="utf-8",
        )
    controller = SkillHotSwapController(SkillRegistry([tmp_path]))
    first = controller.choose("fix pytest failure", ["tests/test_app.py"])
    second = controller.choose("repair Flutter widget", ["lib/main.dart"])
    assert first.active_skill == "python"
    assert second.active_skill == "dart"
    assert second.changed
    assert second.previous_skill == "python"


def test_skill_draft_requires_repeated_success_and_can_write(tmp_path: Path):
    memory = WorkflowMemory()
    for _ in range(3):
        memory.add(
            WorkflowRecord(
                "repair parser",
                "python",
                ("python -m pytest tests/test_parser.py",),
                ("python -m pytest tests/test_parser.py",),
                True,
                ("Preview writes before applying them.",),
            )
        )
    author = SkillDraftAuthor(minimum_successes=3)
    draft = author.draft("parser-repair", "python", memory.records)
    assert draft.ready
    assert draft.evidence_count == 3
    assert "python -m pytest tests/test_parser.py" in draft.content
    preview = author.write(draft, tmp_path)
    assert not preview.exists()
    output = author.write(draft, tmp_path, allow_write=True)
    assert output.exists()
    assert "name: parser-repair" in output.read_text(encoding="utf-8")


def test_workflow_memory_round_trip(tmp_path: Path):
    memory = WorkflowMemory(
        [WorkflowRecord("fix cache", "python", ("pytest",), ("pytest",), True)]
    )
    path = tmp_path / "history.json"
    memory.save(path)
    restored = WorkflowMemory.load(path)
    assert restored.records[0].fingerprint == memory.records[0].fingerprint


def test_context_cache_hits_invalidates_and_round_trips(tmp_path: Path):
    cache = ContextArtifactCache(max_entries=2)
    files = {"a.py": "def a():\n    return 1\n"}
    packet = _packet()
    cache.put("retry auth", files, packet)
    assert cache.get("retry auth", files).hit
    changed = {"a.py": "def a():\n    return 2\n"}
    assert not cache.get("retry auth", changed).hit
    assert cache.invalidate_paths({"a.py"})

    cache.put("retry auth", files, packet)
    path = tmp_path / "cache.json"
    cache.save(path)
    restored = ContextArtifactCache.load(path)
    lookup = restored.get("retry auth", files)
    assert lookup.hit
    assert lookup.packet == packet


def test_repair_orchestrator_completes_low_risk_session():
    orchestrator = RepairOrchestrator(
        RiskAwareModelRouter(RiskAwareModelRouter.default_profiles())
    )
    session = RepairSession("fix parser", 10)
    assert orchestrator.start(session).phase == RepairPhase.SEARCH
    assert orchestrator.record_edit(session, tokens=500).phase == RepairPhase.TEST
    result = orchestrator.record_test(session, passed=True, tokens=200)
    assert result.phase == RepairPhase.COMPLETE
    assert not result.checkpoint_required


def test_repair_orchestrator_escalates_repeated_failure():
    orchestrator = RepairOrchestrator(
        RiskAwareModelRouter(RiskAwareModelRouter.default_profiles())
    )
    session = RepairSession("fix auth", 55)
    orchestrator.start(session)
    orchestrator.record_edit(session, tokens=200)
    first = orchestrator.record_test(
        session,
        passed=False,
        output="AssertionError: token expired",
        tokens=100,
    )
    assert first.phase == RepairPhase.REPAIR
    second = orchestrator.record_test(
        session,
        passed=False,
        output="AssertionError: token expired",
        tokens=100,
    )
    assert second.phase == RepairPhase.ESCALATE
    assert second.checkpoint_required
    assert second.routing is not None


def test_repair_session_pause_save_and_resume(tmp_path: Path):
    orchestrator = RepairOrchestrator(
        RiskAwareModelRouter(RiskAwareModelRouter.default_profiles())
    )
    session = RepairSession("fix tiny budget", 20, token_limit=100)
    orchestrator.start(session)
    paused = orchestrator.record_edit(session, tokens=100)
    assert paused.phase == RepairPhase.PAUSED
    path = tmp_path / "repair.json"
    orchestrator.save(session, path)
    restored = orchestrator.load(path)
    assert restored.phase == RepairPhase.PAUSED
    restored.token_limit = 1000
    resumed = orchestrator.resume(restored)
    assert resumed.phase == RepairPhase.SEARCH


def test_engine_integrates_diversity_routing_skill_swap_and_cache(tmp_path: Path):
    skill = tmp_path / "skills" / "python"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: python\ndescription: Python repair\ntags: [python, pytest]\n---\n"
        "# Workflow\nRun focused tests.\n",
        encoding="utf-8",
    )
    files = {
        "app.py": "def retry_auth():\n    return True\n",
        "tests/test_app.py": (
            "from app import retry_auth\n\ndef test_retry():\n    assert retry_auth()\n"
        ),
    }
    engine = InnovationEngine(tmp_path, skill_roots=[tmp_path / "skills"])
    first = engine.analyze(files, "fix retry_auth pytest", privacy_required=True)
    second = engine.analyze(files, "fix retry_auth pytest", privacy_required=True)
    assert first.diversity.distinct_files >= 1
    assert first.routing.primary.local
    assert first.skill_swap.active_skill == "python"
    assert not first.cache_hit
    assert second.cache_hit
