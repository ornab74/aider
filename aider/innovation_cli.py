"""CLI entry point for Aider's experimental context-surgery layer."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from aider.innovation_actions import ActionParser, GatedSandbox, ToolPolicy
from aider.innovation_context import FloatingContextManager
from aider.innovation_large_files import LargeFileDetector, SearchFirstEditor
from aider.innovation_skills import SkillRegistry


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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "context":
        files = {
            path: Path(path).read_text(encoding="utf-8", errors="replace")
            for path in args.files
        }
        manager = FloatingContextManager(max_tokens=args.max_tokens)
        print(manager.build_packet(files, args.query).render())
        return 0

    if args.command == "large-file":
        path = Path(args.file)
        text = path.read_text(encoding="utf-8", errors="replace")
        detector = LargeFileDetector()
        editor = SearchFirstEditor()
        profile = detector.profile(path, text)
        print(json.dumps(profile.__dict__, indent=2))
        plan = editor.build_search_plan(path, args.query)
        print(f"\nsearch: {plan.ripgrep_command}")
        for window in editor.extract_windows(
            text, args.query, context_lines=args.context_lines
        ):
            print(f"\n## lines {window.start_line}-{window.end_line}\n{window.text}")
        return 0

    if args.command == "skill":
        roots = args.root or [
            "skills", ".aider/skills", str(Path.home() / ".aider/skills")
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

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
