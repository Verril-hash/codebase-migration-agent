"""Command-line interface: ``migrate plan | run | status | evaluate``.

Owner: Adithya (Module 1). Design: SDD section 7.1 (Table 4).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from migration_agent.agent import run_migration
from migration_agent.analysis import StaticAnalyzer
from migration_agent.analysis.scanner import RepositoryScanner
from migration_agent.planning.planner import plan
from migration_agent.spec.loader import load_config, load_spec


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="migrate", description="Codebase-wide migration agent")
    sub = parser.add_subparsers(dest="command", required=True)

    for name, help_text in (
        ("plan", "map call sites and print the batch plan without editing"),
        ("run", "run the full migration loop"),
    ):
        cmd = sub.add_parser(name, help=help_text)
        cmd.add_argument("--spec", required=True, help="migration spec file in specs/")
        cmd.add_argument("--repo", required=True, help="path to the repository to migrate")
        if name == "run":
            cmd.add_argument(
                "--resume", action="store_true", help="resume from the last checkpoint"
            )
            cmd.add_argument(
                "--dry-run", action="store_true", help="analyze and plan without editing"
            )

    status = sub.add_parser("status", help="print the state of every file")
    status.add_argument("--repo", required=True)

    evaluate = sub.add_parser("evaluate", help="run the benchmark over corpus tasks")
    evaluate.add_argument("--tasks", default="corpus", help="directory of benchmark tasks")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "plan":
            spec = load_spec(args.spec)
            analysis = StaticAnalyzer(spec, args.repo).analyze()
            result = plan(analysis.import_graph, analysis.impact_set, batch_size=5)
            print(json.dumps(result.to_dict(), indent=2))
            return 0
        if args.command == "run":
            config = load_config(args.repo, args.spec, dry_run=args.dry_run)
            return 0 if run_migration(config) else 1
        if args.command == "status":
            files = RepositoryScanner(Path(args.repo)).scan()
            print(json.dumps([{"path": path, "state": "pending"} for path in files], indent=2))
            return 0
        print(
            "migrate evaluate: benchmark execution belongs to Module 9 and is not implemented",
            file=sys.stderr,
        )
        return 2
    except (FileNotFoundError, NotADirectoryError, ValueError, OSError, NotImplementedError) as exc:
        print(f"migrate {args.command}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
