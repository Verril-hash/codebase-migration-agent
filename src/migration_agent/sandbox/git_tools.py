"""Checkpoint, diff and roll back batches with Git.

Owner: Prajwal (Module 6). Design: SDD sections 3 and 7.2: the working copy only
changes through Git commits, every batch is checkpointed before it is edited, and
a batch that cannot be repaired is rolled back to that checkpoint.

Checkpoints are commits on the current branch, made under the agent's own name and
without running hooks: a hook is a program from the repository's environment, and
a checkpoint must be quick and must never stop to ask for a signing passphrase.
The agent's state folder (``.migration_agent/``) is never committed or diffed.

A folder that lies inside another repository without being its root is refused.
Benchmark tasks live in ``corpus/<task>/before/`` inside this project, and a commit
made there would land in the project's own history: copy such a folder elsewhere
and call :func:`init_repo` on the copy.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

#: Author of every checkpoint commit, so it is never mistaken for the user's own work.
AGENT_NAME = "migration-agent"
AGENT_EMAIL = "migration-agent@localhost"
#: Folder of agent state that never belongs in a checkpoint or a patch.
STATE_DIR = ".migration_agent"
#: Seconds one git command may take.
GIT_TIMEOUT = 120

_CONFIG = (
    "-c",
    f"user.name={AGENT_NAME}",
    "-c",
    f"user.email={AGENT_EMAIL}",
    "-c",
    "commit.gpgsign=false",
)
_PATHSPEC = ("--", ".", f":(exclude){STATE_DIR}")


class GitError(RuntimeError):
    """A git command failed, or the folder is not a repository the agent may change."""


def _git(repo: Path, *args: str, index: Path | None = None) -> subprocess.CompletedProcess[bytes]:
    """Run one git command in ``repo``; raise :class:`GitError` when it fails."""
    result = _run_git(repo, *args, index=index)
    if result.returncode != 0:
        message = _text(result.stderr) or _text(result.stdout)
        raise GitError(f"git {args[0]} failed in {repo}: {message}")
    return result


def _run_git(
    repo: Path, *args: str, index: Path | None = None
) -> subprocess.CompletedProcess[bytes]:
    # English messages so errors can be recognised, and never an interactive prompt.
    env = {**os.environ, "LC_ALL": "C", "GIT_TERMINAL_PROMPT": "0"}
    if index is not None:
        env["GIT_INDEX_FILE"] = str(index)
    try:
        return subprocess.run(
            ["git", *_CONFIG, "-C", str(repo), *args],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=GIT_TIMEOUT,
            env=env,
            check=False,
        )
    except FileNotFoundError:
        raise GitError("git is not installed or not on PATH") from None
    except subprocess.TimeoutExpired:
        raise GitError(f"git {args[0]} took longer than {GIT_TIMEOUT} s in {repo}") from None


def _text(data: bytes) -> str:
    return data.decode("utf-8", errors="replace").strip()


def _same(a: Path, b: Path) -> bool:
    return os.path.normcase(a.resolve()) == os.path.normcase(b.resolve())


def _folder(repo_path: str | Path) -> Path:
    path = Path(repo_path).resolve()
    if not path.is_dir():
        raise GitError(f"repository folder does not exist: {path}")
    return path


def _toplevel(path: Path) -> Path | None:
    """Root of the repository that contains ``path``, or None when there is none."""
    result = _run_git(path, "rev-parse", "--show-toplevel")
    if result.returncode == 0:
        return Path(_text(result.stdout)).resolve()
    if b"not a git repository" in result.stderr.lower():
        return None
    raise GitError(f"git rev-parse failed in {path}: {_text(result.stderr)}")


def _root(repo_path: str | Path, *, create: bool = False) -> Path:
    """The repository root ``repo_path`` names; with ``create``, initialise a lone folder."""
    path = _folder(repo_path)
    top = _toplevel(path)
    if top is None:
        if not create:
            raise GitError(f"{path} is not a git repository; call init_repo() on it first")
        _git(path, "init", "--quiet", "--initial-branch=main")
        top = _toplevel(path)
    if top is None or not _same(top, path):
        raise GitError(
            f"{path} is inside the git repository {top} but is not its root; "
            "copy it to its own folder and call init_repo() there"
        )
    return path


def _head(root: Path) -> str | None:
    result = _run_git(root, "rev-parse", "--verify", "--quiet", "HEAD^{commit}")
    return _text(result.stdout) if result.returncode == 0 else None


def _commit(root: Path, commit: str) -> str:
    """Full hash of ``commit``; raise when it is not a commit of this repository."""
    if not commit or commit.startswith("-"):
        raise GitError(f"not a commit: {commit!r}")
    result = _run_git(root, "rev-parse", "--verify", "--quiet", f"{commit}^{{commit}}")
    if result.returncode != 0:
        raise GitError(f"unknown commit {commit!r} in {root}")
    return _text(result.stdout)


def init_repo(repo_path: str | Path, message: str = "migration: initial state") -> str:
    """Make ``repo_path`` its own repository, commit its files and return the commit hash.

    Use this on a copy of a benchmark task. A folder that is already a repository
    root is only checkpointed.
    """
    path = _folder(repo_path)
    top = _toplevel(path)
    if top is None or not _same(top, path):
        _git(path, "init", "--quiet", "--initial-branch=main")
    return checkpoint(str(path), message)


def checkpoint(repo_path: str, message: str) -> str:
    """Commit the current state and return the commit hash.

    New, changed and deleted files are all committed; ignored files are not. When
    nothing changed, no commit is made and the current one is returned. A folder that
    is in no repository at all is initialised first.
    """
    root = _root(repo_path, create=True)
    _git(root, "add", "--all", *_PATHSPEC)
    head = _head(root)
    unchanged = _run_git(root, "diff", "--cached", "--quiet").returncode == 0
    if head is not None and unchanged:
        return head
    _git(
        root,
        "commit",
        "--quiet",
        "--no-verify",
        "--allow-empty",
        "-m",
        message.strip() or "migration: checkpoint",
    )
    head = _head(root)
    if head is None:
        raise GitError(f"checkpoint made no commit in {root}")
    return head


def rollback(repo_path: str, commit: str) -> None:
    """Put the working copy back exactly as it was at ``commit``.

    Tracked files are reset and files created since are deleted. Ignored files (a
    virtual environment, caches) and the agent's state folder are kept.
    """
    root = _root(repo_path)
    target = _commit(root, commit)
    _git(root, "reset", "--quiet", "--hard", target)
    _git(root, "clean", "-d", "--force", "--quiet", "-e", STATE_DIR)


def diff(repo_path: str, base: str) -> str:
    """Unified diff from ``base`` to the working copy, new files included.

    The result applies with ``git apply`` or ``patch -p1``: it has ``a/`` and ``b/``
    prefixes, no renames and binary hunks, whatever the user's git configuration says.
    The repository's own index is not touched.
    """
    root = _root(repo_path)
    start = _commit(root, base)
    real_index = Path(_text(_git(root, "rev-parse", "--git-path", "index").stdout))
    if not real_index.is_absolute():
        real_index = root / real_index
    with tempfile.TemporaryDirectory(prefix="migration-diff-") as scratch:
        index = Path(scratch) / "index"
        if real_index.is_file():
            shutil.copyfile(real_index, index)
        _git(root, "add", "--all", *_PATHSPEC, index=index)
        result = _git(
            root,
            "diff",
            "--cached",
            "--binary",
            "--no-color",
            "--no-ext-diff",
            "--no-textconv",
            "--no-renames",
            "--src-prefix=a/",
            "--dst-prefix=b/",
            start,
            *_PATHSPEC,
            index=index,
        )
    return result.stdout.decode("utf-8", errors="replace")
