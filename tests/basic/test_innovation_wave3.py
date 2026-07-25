import json
from pathlib import Path

from aider.innovation_diff import LargeDiffFolder, invert_unified_diff
from aider.innovation_engine import InnovationEngine
from aider.innovation_failures import FailureLocalizer
from aider.innovation_graph import PythonCallGraph
from aider.innovation_mcp import MCPSchemaSlimmer
from aider.innovation_semantic_edit import SemanticSedPlanner
from aider.innovation_tests import CounterfactualTestSelector


def test_call_chain_packet_and_dependency_heat_map():
    files = {
        "app/service.py": (
            "from app.helpers import normalize\n\n"
            "def validate(value):\n    return value > 0\n\n"
            "def run(value):\n    if validate(value):\n        return normalize(value)\n"
        ),
        "app/helpers.py": "def normalize(value):\n    return value + 1\n",
    }
    graph = PythonCallGraph(files)
    packet = graph.packet("trace run normalize", depth=1, max_tokens=1200)
    names = {node.qualified_name for node in packet.nodes}
    assert "app.service.run" in names
    assert "app.service.validate" in names
    assert "app.helpers.normalize" in names
    heat = graph.heat_map("validate", touches={"app/service.py": 3})
    assert heat[0].path == "app/service.py"


def test_failure_localizer_omits_unrelated_files():
    output = """________________ test_run ________________
tests/test_service.py:5: in test_run
>       assert run(2) == 3
E       assert 4 == 3
app/service.py:8: in run
    return value * 2
FAILED tests/test_service.py::test_run - assert 4 == 3
"""
    files = {
        "tests/test_service.py": "from app.service import run\n\ndef test_run():\n    assert run(2) == 3\n",
        "app/service.py": "def run(value):\n    return value * 2\n",
        "app/unrelated.py": "x = 1\n" * 100,
    }
    localizer = FailureLocalizer()
    packet = localizer.build_packet(localizer.parse(output), files, context_lines=3)
    assert {item.path for item in packet.slices} == {"app/service.py", "tests/test_service.py"}
    assert "app/unrelated.py" not in packet.render()


def test_counterfactual_test_selection_prefers_import_reference():
    selection = CounterfactualTestSelector().select(
        changed_paths=["app/service.py"],
        changed_symbols=["app.service.run"],
        tests={
            "tests/test_service.py": "from app.service import run\n\ndef test_run():\n    assert run(1) == 2\n",
            "tests/test_other.py": "def test_other():\n    assert True\n",
        },
    )
    assert selection.candidates[0].path == "tests/test_service.py"
    assert selection.command == "pytest -q tests/test_service.py"


def test_mcp_schema_slimmer_preserves_required_fields():
    schema = {
        "tools": [
            {
                "name": "search_repository",
                "description": "Search code by query and path",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "code query"},
                        "path": {"type": "string", "description": "repository path"},
                        "page": {"type": "integer", "default": 1, "examples": [1]},
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "send_email",
                "description": "Send an email message",
                "inputSchema": {
                    "type": "object",
                    "properties": {"to": {"type": "string"}, "body": {"type": "string"}},
                    "required": ["to", "body"],
                },
            },
        ]
    }
    result = MCPSchemaSlimmer().slim(schema, "search repository code query path", max_tools=1)
    assert result.selected_tools == ("search_repository",)
    assert result.schema["tools"][0]["inputSchema"]["required"] == ["query"]
    assert result.slim_token_estimate < result.original_token_estimate


def test_large_diff_folding_and_inversion():
    diff = """diff --git a/demo.py b/demo.py
--- a/demo.py
+++ b/demo.py
@@ -1,3 +1,4 @@
-old = 1
+new = 2
+log(value)
+log(value)
+log(value)
 keep = True
"""
    report = LargeDiffFolder().fold(diff)
    assert report.files[0].repeated_additions == (("log(value)", 3),)
    inverted = invert_unified_diff(diff)
    assert "@@ -1,4 +1,3 @@" in inverted
    assert "+old = 1" in inverted


def test_semantic_sed_replans_after_symbol_moves(tmp_path: Path):
    path = tmp_path / "demo.py"
    original = "class Demo:\n    def run(self, value):\n        return value + 1\n"
    moved = "\n\nclass Demo:\n\n    def run( self, value ):\n        return value + 1\n"
    planner = SemanticSedPlanner()
    initial = planner.plan(path, original, "Demo.run", "    def run(self, value):\n        return value + 2")
    replanned = planner.replan(initial.anchor, moved, "    def run(self, value):\n        return value + 2")
    assert replanned.resolution.confidence >= 0.8
    assert replanned.edit.start_line > initial.edit.start_line
    assert "sha256sum" in replanned.sed_script


def test_engine_coordinates_wave3_features(tmp_path: Path):
    files = {
        "app/service.py": "def run(value):\n    return value + 1\n",
        "tests/test_service.py": "from app.service import run\n\ndef test_run():\n    assert run(1) == 2\n",
    }
    engine = InnovationEngine(tmp_path, max_tokens=1400)
    report = engine.analyze(files, "inspect run")
    assert report.call_chain.nodes
    assert report.dependency_heat
    slim = engine.slim_mcp(
        {"tools": [{"name": "read_file", "description": "read source file", "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}]},
        "read source file path",
    )
    assert slim.selected_tools == ("read_file",)
    json.dumps(slim.schema)
