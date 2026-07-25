from pathlib import Path

from aider.innovation_actions import ActionParser, RiskLevel, ToolPolicy
from aider.innovation_skills import SkillRegistry


def test_skill_discovery_modifier_and_summary(tmp_path: Path):
    skill_dir = tmp_path / "python"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: python\ndescription: Python testing workflow\ntags: [python, pytest]\n---\n"
        "# Workflow\nRun pytest.\n# Safety\nUse a virtual environment.\n",
        encoding="utf-8",
    )
    registry = SkillRegistry([tmp_path])
    matches = registry.find("python pytest")
    assert matches[0].skill.name == "python"
    assert "Run pytest" in matches[0].summary
    modifier, cleaned = registry.resolve_modifier("@skill:python fix tests")
    assert modifier == "python"
    assert cleaned == "fix tests"


def test_action_parser_and_policy(tmp_path: Path):
    parser = ActionParser()
    action = parser.parse('[action:terminal cwd="."]rg -n target .[/action]')[0]
    decision = ToolPolicy(tmp_path).evaluate(action)
    assert decision.allowed
    assert decision.risk == RiskLevel.READ_ONLY


def test_policy_blocks_destructive_command(tmp_path: Path):
    action = ActionParser().parse("[action:terminal]rm -rf .[/action]")[0]
    decision = ToolPolicy(tmp_path, allow_workspace_writes=True).evaluate(action)
    assert not decision.allowed
    assert decision.risk == RiskLevel.DESTRUCTIVE


def test_approval_does_not_bypass_disabled_write_policy(tmp_path: Path):
    action = ActionParser().parse("[action:terminal]touch created.txt[/action]")[0]
    policy = ToolPolicy(tmp_path, allow_workspace_writes=False)
    from aider.innovation_actions import GatedSandbox

    result = GatedSandbox(policy).run(action, approved=True)
    assert result.returncode == 126
    assert not (tmp_path / "created.txt").exists()
