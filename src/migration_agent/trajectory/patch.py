"""Write the final unified .patch for the whole migration.

Owner: Prabanjan (Module 8).

The patch is generated from a temporary Git index so the repository's own
index and working tree are never changed as a side effect.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path


class PatchError(RuntimeError):
    """The patch could not be produced."""


def _git(repo: Path, *args: str, env: dict[str, str] | None = None) -> bytes:
    """Run Git in ``repo`` and raise a descriptive error on failure."""
    try:
        result = subprocess.run(
            ["git", "-c", "core.quotepath=off", *args],
            cwd=repo,
            env=env,
            capture_output=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise PatchError("git is not installed or not on PATH") from exc
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", errors="replace").strip()
        command = " ".join(args)
        raise PatchError(f"git command failed in {repo}: {command}: {message}")
    return result.stdout


def emit_patch(repo_path: str, base_commit: str, out_path: Path) -> Path:
    """Write the unified diff from ``base_commit`` to the current worktree."""
    repo = Path(repo_path)
    if not repo.is_dir():
        raise PatchError(f"repository path does not exist: {repo}")
    if not base_commit or base_commit.startswith("-"):
        raise PatchError(f"not a valid commit: {base_commit!r}")

    _git(repo, "rev-parse", "--is-inside-work-tree")
    base = _git(repo, "rev-parse", "--verify", f"{base_commit}^{{commit}}").decode().strip()

    with tempfile.TemporaryDirectory() as scratch:
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(scratch) / "index")}
        _git(repo, "read-tree", base, env=env)
        _git(repo, "add", "--all", env=env)
        diff = _git(
            repo,
            "diff",
            "--cached",
            "--binary",
            "--find-renames",
            "--no-color",
            "--no-ext-diff",
            "--no-textconv",
            "--src-prefix=a/",
            "--dst-prefix=b/",
            base,
            env=env,
        )

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    temporary = out.with_name(out.name + ".tmp")
    temporary.write_bytes(diff)
    os.replace(temporary, out)
    return out
