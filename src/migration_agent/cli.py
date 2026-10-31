"""Command-line interface: ``migrate plan | run | status | evaluate``.

Owner: Adithya (Module 1). Design: SDD section 7.1 (Table 4).
"""

from __future__ import annotations

import argparse
import sys


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

    status = sub.add_parser("status", help="print the state of every file")
    status.add_argument("--repo", required=True)

    evaluate = sub.add_parser("evaluate", help="run the benchmark over corpus tasks")
    evaluate.add_argument("--tasks", default="corpus", help="directory of benchmark tasks")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    print(f"migrate {args.command}: not implemented yet (Module 1, Adithya)", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
