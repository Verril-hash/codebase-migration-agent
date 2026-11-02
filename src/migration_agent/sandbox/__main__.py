"""``python -m migration_agent.sandbox``: build the sandbox images and check Docker.

Owner: Prajwal (Module 6).
"""

from __future__ import annotations

import argparse
import sys

from migration_agent.sandbox.container import SandboxError, docker_available, image_exists
from migration_agent.sandbox.image import DEFAULT_PROFILE, PROFILES, build_image, image_tag


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m migration_agent.sandbox", description="Module 6 sandbox images"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build", help="build a sandbox image (needs the network once)")
    build.add_argument("--profile", choices=[*PROFILES, "all"], default=DEFAULT_PROFILE)
    build.add_argument(
        "--with",
        dest="extra",
        action="append",
        default=[],
        metavar="PACKAGE==VERSION",
        help="another pinned package the repository's tests need; repeatable",
    )
    sub.add_parser("check", help="report whether Docker runs and which images are built")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "check":
        running = docker_available()
        print(f"docker engine: {'running' if running else 'NOT running'}")
        for profile in PROFILES:
            built = running and image_exists(image_tag(profile))
            print(f"{image_tag(profile)}: {'built' if built else 'missing'}")
        return 0 if running else 1
    profiles = list(PROFILES) if args.profile == "all" else [args.profile]
    for profile in profiles:
        print(f"building {image_tag(profile)} ...", flush=True)
        try:
            print(f"built {build_image(profile, args.extra)}")
        except (SandboxError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
