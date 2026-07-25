from pathlib import Path

from aider.innovation_actions import ActionParser, RiskLevel, ToolPolicy
from aider.innovation_context import FloatingContextManager, FloatingContextState
from aider.innovation_large_files import LargeFileDetector, SearchFirstEditor
from aider.innovation_skills import SkillRegistry


def test_context_packet_prefers_query_and_explicit_file():
    files = {
        "small.py": "def unrelated():\n    return 1\n",
        "target.py": "\n".join(
            ["# filler"] * 40 + ["def calculate_budget():", "    return 42"]
        ),
    }
    state = FloatingContextState()
    state.pin("target.py")
    manager = FloatingContextManager(max_tokens=1000, reserve_tokens=200, state=state)
    packet = manager.build_packet(
        files, "fix calculate_budget", explicit_paths=["target.py"]
    )
    assert packet.slices
    assert packet.slices[0].path == "target.py"
    assert packet.token_estimate <= packet.budget


def test_large_file_detector_and_search_windows():
    text = "\n".join(
        ["filler"] * 50 + ["def target_symbol():", "    return 1"] + ["tail"] * 50
    )
    detector = LargeFileDetector(
        line_threshold=80, byte_threshold=10_000, token_threshold=10_000
    )
    assert detector.profile("demo.py", text).is_large
    windows = SearchFirstEditor().extract_windows(text, "target_symbol", context_lines=2)
    assert len(windows) == 1
    assert "target_symbol" in windows[0].text


def test_surgical_edit_preview_apply_and_stale_guard(tmp_path: Path):
    path = tmp_path / "demo.py"
    original = "one\ntwo\nthree\n"
    path.write_text(original, encoding="utf-8")
    editor = SearchFirstEditor()
    edit = editor.make_edit(path, original, start_line=2, end_line=2, replacement="TWO")
    preview = editor.apply(edit)
    assert preview.changed
    assert "-two" in preview.diff
    assert path.read_text(encoding="utf-8") == original
    editor.apply(edit, allow_write=True)
    assert path.read_text(encoding="utf-8") == "one\nTWO\nthree\n"
    try:
        editor.apply(edit, allow_write=True)
    except RuntimeError as exc:
        assert "stale" in str(exc)
    else:
        raise AssertionError("expected stale edit refusal")


def test_skill_discovery_modifier_and_summary(tmp_path: Path):
    skill_dir = tmp_path / "python"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: python\ndescription: Python testing workflow\n"
        "tags: [python, pytest]\n---\n# Workflow\nRun pytest.\n"
        "# Safety\nUse a virtual environment.\n",
        encoding="utf-8",
    )
    registry = SkillRegistry([tmp_path])
    matches = registry.find("python pytest")
    assert matches[0].skill.name == "python"
    assert "Run pytest" in matches[0].summary
    modifier, cleaned = registry.resolve_modifier("@skill:python fix tests")
    assert modifier == "python"
    assert cleaned == "fix tests"


def test_action_policy_read_only_write_and_destructive(tmp_path: Path):
    parser = ActionParser()
    read_action = parser.parse(
        '[action:terminal cwd="."]rg -n target .[/action]'
    )[0]
    read_decision = ToolPolicy(tmp_path).evaluate(read_action)
    assert read_decision.allowed
    assert read_decision.risk == RiskLevel.READ_ONLY

    write_action = parser.parse("[action:terminal]touch output.txt[/action]")[0]
    write_decision = ToolPolicy(tmp_path).evaluate(write_action)
    assert not write_decision.allowed
    assert write_decision.risk == RiskLevel.WORKSPACE_WRITE

    destructive = parser.parse("[action:terminal]rm -rf .[/action]")[0]
    destructive_decision = ToolPolicy(
        tmp_path, allow_workspace_writes=True
    ).evaluate(destructive)
    assert not destructive_decision.allowed
    assert destructive_decision.risk == RiskLevel.DESTRUCTIVE
