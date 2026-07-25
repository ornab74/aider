from pathlib import Path

from aider.innovation_actions import ActionParser, GatedSandbox, ToolPolicy
from aider.innovation_anchors import SymbolAnchorIndex
from aider.innovation_budgets import AdaptiveBudgetPlanner, BudgetExceeded, WorkStage
from aider.innovation_capsules import (
    ContextCapsule,
    ContextLeaseBook,
    ContextProvenanceLedger,
)
from aider.innovation_context import FloatingContextManager, FloatingContextState
from aider.innovation_engine import InnovationEngine
from aider.innovation_risk import BlastRadiusAnalyzer, PatchQuorum


def test_context_leases_expire_and_capsule_round_trips(tmp_path: Path):
    files = {"demo.py": "def target():\n    return 1\n"}
    state = FloatingContextState()
    state.pin("demo.py")
    packet = FloatingContextManager(
        max_tokens=800, reserve_tokens=200, state=state
    ).build_packet(files, "target")
    leases = ContextLeaseBook(default_turns=2)
    identifiers = leases.reconcile(packet)
    provenance = ContextProvenanceLedger()
    assert provenance.record_packet(packet) == identifiers

    leases.advance()
    assert leases.is_active(identifiers[0])
    assert leases.advance() == identifiers
    assert not leases.is_active(identifiers[0])

    leases.reconcile(packet)
    capsule = ContextCapsule.capture(packet, state, leases, provenance)
    path = tmp_path / "context.capsule.json"
    capsule.save(path)
    restored = ContextCapsule.load(path)
    assert restored.query == "target"
    assert restored.verify(files).valid
    changed = {"demo.py": "def target():\n    return 2\n"}
    assert not restored.verify(changed).valid


def test_python_anchor_survives_reformat_and_tracks_symbol_move():
    original = """class Demo:\n    def run(self, value):\n        return value + 1\n"""
    reformatted = """\n\nclass Demo:\n\n    def run( self, value ):\n        # formatting changed\n        return value + 1\n"""
    index = SymbolAnchorIndex()
    anchor = index.create(original, "Demo.run", path="demo.py")
    resolution = index.resolve(reformatted, anchor)
    assert resolution.matched is not None
    assert resolution.confidence == 1.0
    assert resolution.matched.start_line > anchor.start_line


def test_budget_planner_moves_capacity_to_verification_for_risk():
    planner = AdaptiveBudgetPlanner()
    low = planner.plan(total_tokens=4000, total_seconds=100, risk_score=0)
    high = planner.plan(total_tokens=4000, total_seconds=100, risk_score=100)
    assert sum(item.token_limit for item in low.budgets.values()) == 4000
    assert high.budgets[WorkStage.TEST].token_limit > low.budgets[WorkStage.TEST].token_limit
    assert high.budgets[WorkStage.REPAIR].token_limit > low.budgets[WorkStage.REPAIR].token_limit
    low.consume(WorkStage.SEARCH, tokens=100, seconds=1)
    try:
        low.consume(WorkStage.SEARCH, tokens=10_000)
    except BudgetExceeded:
        pass
    else:
        raise AssertionError("expected bounded stage budget")


def test_blast_radius_and_patch_quorum():
    diff = """diff --git a/aider/security/auth.py b/aider/security/auth.py
--- a/aider/security/auth.py
+++ b/aider/security/auth.py
@@ -1,2 +1,3 @@
-def login(user):
+def login(user, token):
+    validate(token)
     return user
"""
    report = BlastRadiusAnalyzer().analyze(diff)
    assert report.band in {"high", "critical"}
    assert report.requires_quorum
    quorum = PatchQuorum()
    quorum.submit("model-a", diff)
    assert not quorum.reached(report.minimum_approvals)
    quorum.submit("model-b", diff + "\n")
    assert quorum.reached(2)


def test_one_time_capability_token_is_action_bound(tmp_path: Path):
    action = ActionParser().parse("[action:terminal]touch output.txt[/action]")[0]
    other = ActionParser().parse("[action:terminal]touch other.txt[/action]")[0]
    sandbox = GatedSandbox(ToolPolicy(tmp_path, allow_workspace_writes=True))
    token = sandbox.issue_capability(action)
    result = sandbox.run(action, capability_token=token)
    assert result.returncode == 0
    assert (tmp_path / "output.txt").exists()
    replay = sandbox.run(action, capability_token=token)
    assert replay.returncode == 125
    substituted = sandbox.run(other, capability_token=token)
    assert substituted.returncode == 125
    assert not (tmp_path / "other.txt").exists()


def test_engine_coordinates_risk_budgets_anchors_and_checkpoint(tmp_path: Path):
    engine = InnovationEngine(tmp_path, max_tokens=1000)
    files = {"demo.py": "def target():\n    return 1\n"}
    report = engine.analyze(files, "fix target", diff="")
    assert report.context_slice_ids
    assert report.symbol_anchors["demo.py"][0].qualified_name == "target"
    assert report.budgets["search"]["token_limit"] > 0
    capsule_path = tmp_path / "resume.json"
    engine.checkpoint(capsule_path)
    assert ContextCapsule.load(capsule_path).verify(files).valid
