"""CLI entry point for Aider's experimental context-surgery layer."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from aider.innovation_actions import ActionParser, GatedSandbox, ToolPolicy
from aider.innovation_context import FloatingContextManager
from aider.innovation_diff import LargeDiffFolder, invert_unified_diff
from aider.innovation_failures import FailureLocalizer
from aider.innovation_graph import PythonCallGraph
from aider.innovation_large_files import LargeFileDetector, SearchFirstEditor
from aider.innovation_mcp import MCPSchemaSlimmer
from aider.innovation_semantic_edit import SemanticSedPlanner
from aider.innovation_skills import SkillRegistry
from aider.innovation_tests import CounterfactualTestSelector


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aider-innovate")
    subparsers = parser.add_subparsers(dest="command", required=True)

    context = subparsers.add_parser("context", help="build a query-focused context packet")
    context.add_argument("query")
    context.add_argument("files", nargs="+")
    context.add_argument("--max-tokens", type=int, default=4096)

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

    chain = subparsers.add_parser("call-chain", help="build a bounded caller/callee packet")
    chain.add_argument("query")
    chain.add_argument("files", nargs="+")
    chain.add_argument("--depth", type=int, default=1)
    chain.add_argument("--max-tokens", type=int, default=2200)
    chain.add_argument("--heat", action="store_true")

    failure = subparsers.add_parser("failure", help="localize pytest output")
    failure.add_argument("output")
    failure.add_argument("files", nargs="+")
    failure.add_argument("--max-tokens", type=int, default=1600)

    select = subparsers.add_parser("select-tests", help="rank tests for changed code")
    select.add_argument("tests", nargs="+")
    select.add_argument("--changed", action="append", required=True)
    select.add_argument("--symbol", action="append", default=[])

    slim = subparsers.add_parser("slim-mcp", help="remove unrelated MCP tools and fields")
    slim.add_argument("schema")
    slim.add_argument("query")
    slim.add_argument("--max-tools", type=int, default=6)

    fold = subparsers.add_parser("fold-diff", help="summarize repeated diff changes")
    fold.add_argument("diff")
    fold.add_argument("--invert", action="store_true")

    semantic = subparsers.add_parser("semantic-sed", help="build an AST-resolved SED plan")
    semantic.add_argument("file")
    semantic.add_argument("symbol")
    semantic.add_argument("replacement")
    return parser


def _read_files(paths: list[str]) -> dict[str, str]:
    return {
        path: Path(path).read_text(encoding="utf-8", errors="replace")
        for path in paths
    }


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "context":
        manager = FloatingContextManager(max_tokens=args.max_tokens)
        print(manager.build_packet(_read_files(args.files), args.query).render())
        return 0

    if args.command == "large-file":
        path = Path(args.file)
        text = path.read_text(encoding="utf-8", errors="replace")
        detector = LargeFileDetector()
        editor = SearchFirstEditor()
        print(json.dumps(detector.profile(path, text).__dict__, indent=2))
        print(f"\nsearch: {editor.build_search_plan(path, args.query).ripgrep_command}")
        for window in editor.extract_windows(text, args.query, context_lines=args.context_lines):
            print(f"\n## lines {window.start_line}-{window.end_line}\n{window.text}")
        return 0

    if args.command == "skill":
        roots = args.root or ["skills", ".aider/skills", str(Path.home() / ".aider/skills")]
        matches = SkillRegistry(roots).find(args.query, limit=args.limit)
        for match in matches:
            print(f"\n=== {match.skill.name} ({match.score:.2f}) ===\n{match.summary}")
        return 0 if matches else 1

    if args.command == "action":
        actions = ActionParser().parse(args.request)
        if not actions:
            print("No [action:tool]...[/action] block found", file=sys.stderr)
            return 2
        sandbox = GatedSandbox(
            ToolPolicy(
                args.workspace,
                allow_network=args.allow_network,
                allow_workspace_writes=args.allow_write,
            )
        )
        exit_code = 0
        for request in actions:
            print(sandbox.manifest(request))
            if args.execute:
                result = sandbox.run(request, approved=args.approve)
                if result.stdout:
                    print(result.stdout, end="")
                if result.stderr:
                    print(result.stderr, file=sys.stderr)
                exit_code = max(exit_code, result.returncode)
        return exit_code

    if args.command == "call-chain":
        graph = PythonCallGraph(_read_files(args.files))
        print(graph.packet(args.query, depth=args.depth, max_tokens=args.max_tokens).render())
        if args.heat:
            print(json.dumps([item.__dict__ for item in graph.heat_map(args.query)], indent=2))
        return 0

    if args.command == "failure":
        output = sys.stdin.read() if args.output == "-" else Path(args.output).read_text()
        localizer = FailureLocalizer()
        packet = localizer.build_packet(
            localizer.parse(output), _read_files(args.files), max_tokens=args.max_tokens
        )
        print(packet.render())
        return 0

    if args.command == "select-tests":
        result = CounterfactualTestSelector().select(
            changed_paths=args.changed,
            changed_symbols=args.symbol,
            tests=_read_files(args.tests),
        )
        print(json.dumps([item.__dict__ for item in result.candidates], indent=2))
        print(result.command)
        return 0 if result.candidates else 1

    if args.command == "slim-mcp":
        schema = json.loads(Path(args.schema).read_text(encoding="utf-8"))
        result = MCPSchemaSlimmer().slim(schema, args.query, max_tools=args.max_tools)
        print(json.dumps(result.schema, indent=2))
        print(
            f"# tokens {result.original_token_estimate} -> {result.slim_token_estimate}; "
            f"reduction={result.reduction_ratio:.1%}",
            file=sys.stderr,
        )
        return 0

    if args.command == "fold-diff":
        diff = sys.stdin.read() if args.diff == "-" else Path(args.diff).read_text()
        print(LargeDiffFolder().fold(diff).render())
        if args.invert:
            print(invert_unified_diff(diff))
        return 0

    if args.command == "semantic-sed":
        path = Path(args.file)
        text = path.read_text(encoding="utf-8")
        replacement = Path(args.replacement).read_text(encoding="utf-8")
        print(SemanticSedPlanner().plan(path, text, args.symbol, replacement).sed_script)
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
