"""Build the sandbox images the tests and the linter run in.

Owner: Prajwal (Module 6). Design: SDD table 5: the sandbox has no network, so
every package is installed once, pinned, when the image is built.

There is one image per profile, one profile per library version the agent needs
to test against. Build them with ``python -m migration_agent.sandbox build``.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
from collections.abc import Sequence

from migration_agent.sandbox.container import WORKDIR, SandboxError

BASE_IMAGE = "python:3.12.16-slim"
TOOLS = ("pytest==9.2.0", "ruff==0.17.3")
#: Profile -> the pinned packages that make it.
PROFILES: dict[str, tuple[str, ...]] = {
    "sqla-1.4": ("SQLAlchemy==1.4.54",),
    "sqla-2.0": ("SQLAlchemy==2.0.55",),
}
#: The migration's target version: tests judge the migrated code against it.
DEFAULT_PROFILE = "sqla-2.0"
REPOSITORY = "migration-agent-sandbox"

_PINNED = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*(\[[A-Za-z0-9._,-]+\])?==[A-Za-z0-9.+!_-]+$")


def image_tag(profile: str) -> str:
    """Local tag of a profile's image, e.g. ``migration-agent-sandbox:sqla-2.0``."""
    if profile not in PROFILES:
        raise ValueError(f"unknown sandbox profile {profile!r}; choose from {sorted(PROFILES)}")
    return f"{REPOSITORY}:{profile}"


def default_image() -> str:
    """Image to run in: ``SANDBOX_IMAGE`` when set, else the default profile's image."""
    return os.environ.get("SANDBOX_IMAGE", "").strip() or image_tag(DEFAULT_PROFILE)


def dockerfile(packages: Sequence[str]) -> str:
    """Dockerfile of an image with Python 3.12 and exactly ``packages`` installed."""
    return "\n".join(
        [
            f"FROM {BASE_IMAGE}",
            "ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 "
            "PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_ROOT_USER_ACTION=ignore",
            f"RUN python -m pip install {' '.join(shlex.quote(p) for p in packages)}",
            f"WORKDIR {WORKDIR}",
            "",
        ]
    )


def build_image(profile: str = DEFAULT_PROFILE, extra: Sequence[str] = ()) -> str:
    """Build a profile's image and return its tag.

    ``extra`` adds packages the repository's tests need, each pinned as
    ``name==version``. Building needs the network; running the image does not.
    """
    tag = image_tag(profile)
    for package in extra:
        if not _PINNED.match(package):
            raise ValueError(f"extra packages must be pinned as name==version, got {package!r}")
    text = dockerfile([*TOOLS, *PROFILES[profile], *extra])
    try:
        result = subprocess.run(
            ["docker", "build", "--tag", tag, "-"],
            input=text.encode("utf-8"),
            capture_output=True,
            timeout=1800,
            check=False,
        )
    except FileNotFoundError:
        raise SandboxError("Docker is not installed or not on PATH") from None
    except subprocess.TimeoutExpired:
        raise SandboxError(f"building {tag} took longer than 30 minutes") from None
    if result.returncode != 0:
        output = (result.stderr or result.stdout).decode("utf-8", errors="replace")
        raise SandboxError(f"building {tag} failed:\n{output[-4000:]}")
    return tag
