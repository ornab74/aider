import json
from pathlib import Path

from aider.innovation_arena import PatchCandidate, SpeculativePatchArena
from aider.innovation_causal import CausalFailureAnalyzer
from aider.innovation_context import ContextPacket, ContextSlice
from aider.innovation_contracts import (
    InvariantContractExtractor,
    InvariantContractValidator,
)
from aider.innovation_engine import InnovationEngine
from aider.innovation_mutation import MutationTestPlanner
from aider.innovation_pressure import ContextPressureMonitor
from aider.innovation_trajectory import (
    RepairTrajectoryMemory,
    TrajectoryEvent,
)


def _baseline() -> dict[str, str]:
    return {
        "auth.py": (
            "# invariant: retry must remain bounded\n"
            "def retry_auth(token, attempts):\n"
            "    return token if attempts < 3 else None\n"
        )
    }


def test_contract_validator_preserves_public_signature():
    baseline = _baseline()
    contracts = InvariantContractExtractor().extract_many(baseline)
    candidate = {
        "auth.py": (
            "# invariant: retry must remain bounded\n"
            "def retry_auth(token, attempts):\n"
            "    return token if attempts <= 2 else None\n"
        )
    }
    result = InvariantContractValidator().validate(contracts, candidate)
    assert result.valid
    assert result.blocking_count == 0


def test_contract_validator_blocks_removed_symbol_and_syntax_error():
    contracts = InvariantContractExtractor().extract_many(_baseline())
    result = InvariantContractValidator().validate(
        contracts,
        {"auth.py": "def renamed(:\n    pass\n"},
    )
    assert not result.valid
    assert result.blocking_count >= 2


def test_mutation_planner_targets_boundaries_and_selects_tests():
    files = {
        "auth.py": (
            "def retry_auth(token, attempts):\n"
            "    if attempts < 3:\n"
            "        return token\n"
            "    return None\n"
        )
    }
    tests = {
        "tests/test_auth.py": (
            "from auth import retry_auth\n\n"
            "def test_retry_limit():\n"
            "    assert retry_auth('x', 2) == 'x'\n"
        )
    }
    plan = MutationTestPlanner().plan(
        files,
        changed_paths=["auth.py"],
        changed_symbols=["retry_auth"],
        tests=tests,
    )
    assert any(item.replacement == ">=" for item in plan.targets)
    assert plan.selected_tests.candidates[0].path == "tests/test_auth.py"


def test_causal_failure_analyzer_prefers_changed_trace_symbol():
    files = {
        "auth.py": (
            "def retry_auth(token, attempts):\n"
            "    if attempts < 3:\n"
            "        return token\n"
            "    return None\n"
        ),
        "service.py": (
            "from auth import retry_auth\n\n"
            "def login(token):\n"
            "    return retry_auth(token, 3)\n"
        ),
    }
    output = (
        "________________ test_login ________________\n"
        "auth.py:2: in retry_auth\n"
        "> assert retry_auth('x', 3) == 'x'\n"
        "E AssertionError: token expired\n"
    )
    report = CausalFailureAnalyzer().analyze(
        output,
        files,
        changed_paths=["auth.py"],
    )
    assert report.hypotheses
    assert report.hypotheses[0].path == "auth.py"
    assert "recently changed" in report.hypotheses[0].reasons


def test_patch_arena_rejects_contract_break_and_picks_valid_candidate():
    baseline = _baseline()
    contracts = InvariantContractExtractor().extract_many(baseline)
    good_text = (
        "# invariant: retry must remain bounded\n"
        "def retry_auth(token, attempts):\n"
        "    return token if attempts <= 2 else None\n"
    )
    candidates = [
        PatchCandidate(
            "break-api",
            {"auth.py": "def retry(token):\n    return token\n"},
            "diff --git a/auth.py b/auth.py\n-def retry_auth(token, attempts):\n+def retry(token):\n",
        ),
        PatchCandidate(
            "bounded-fix",
            {"auth.py": good_text},
            "diff --git a/auth.py b/auth.py\n-    return token if attempts < 3 else None\n+    return token if attempts <= 2 else None\n",
            ("tests/test_auth.py",),
        ),
    ]
    report = SpeculativePatchArena().evaluate(candidates, contracts)
    assert report.winner is not None
    assert report.winner.candidate.name == "bounded-fix"
    assert not report.evaluations[-1].contracts.valid


def test_trajectory_memory_compresses_loops_and_recalls_success(tmp_path: Path):
    memory = RepairTrajectoryMemory()
    failure = "same-failure"
    summary = memory.summarize(
        "repair auth retry",
        [
            TrajectoryEvent("search", "inspect auth", "found", 100),
            TrajectoryEvent("test", "run auth tests", "failed", 80, failure),
            TrajectoryEvent("repair", "adjust boundary", "edited", 120),
            TrajectoryEvent("test", "run auth tests", "failed", 80, failure),
        ],
        success=False,
    )
    assert summary.loop_detected
    memory.summarize(
        "repair auth retry",
        [TrajectoryEvent("test", "run auth tests", "passed", 50)],
        success=True,
    )
    matches = memory.recall("auth retry")
    assert matches[0].summary.success
    path = tmp_path / "trajectory.json"
    memory.save(path)
    assert RepairTrajectoryMemory.load(path).summaries == memory.summaries


def test_context_pressure_detects_saturation_redundancy_and_churn():
    text = "retry auth token failure " * 20
    packet = ContextPacket(
        "retry auth",
        (
            ContextSlice("a.py", 1, 20, text, 10.0),
            ContextSlice("a.py", 21, 40, text, 9.0),
            ContextSlice("b.py", 1, 20, text, 8.0),
        ),
        95,
        100,
    )
    report = ContextPressureMonitor().assess(
        packet,
        previous_slice_ids=("old-a", "old-b"),
        current_slice_ids=("new-a", "new-b"),
    )
    assert report.band in {"high", "critical"}
    assert report.redundancy > 0.9
    assert report.churn == 1.0
    assert any("diversity" in item for item in report.recommendations)


def test_engine_wave5_contract_pressure_mutation_and_arena(tmp_path: Path):
    files = {
        "auth.py": (
            "def retry_auth(token, attempts):\n"
            "    return token if attempts < 3 else None\n"
        ),
        "tests/test_auth.py": (
            "from auth import retry_auth\n\n"
            "def test_retry():\n"
            "    assert retry_auth('x', 2) == 'x'\n"
        ),
    }
    engine = InnovationEngine(tmp_path)
    report = engine.analyze(files, "fix retry_auth boundary")
    assert report.contracts
    assert report.pressure.band in {"low", "medium", "high", "critical"}
    mutation = engine.plan_mutations(
        files,
        changed_paths=["auth.py"],
        changed_symbols=["retry_auth"],
        tests={"tests/test_auth.py": files["tests/test_auth.py"]},
    )
    assert mutation.targets
    validation = engine.validate_candidate(files, files)
    assert validation.valid


def test_patch_arena_json_shape_is_serializable():
    contracts = InvariantContractExtractor().extract_many(_baseline())
    report = SpeculativePatchArena().evaluate(
        [PatchCandidate("same", _baseline(), "", ("tests/test_auth.py",))],
        contracts,
    )
    payload = json.dumps(
        {
            "winner": report.winner.candidate.name if report.winner else None,
            "scores": [item.score for item in report.evaluations],
        }
    )
    assert '"winner": "same"' in payload
