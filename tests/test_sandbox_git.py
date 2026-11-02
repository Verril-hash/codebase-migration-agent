"""Tests for Module 6: Git checkpoints, rollback and diff on real repositories."""

import os
import subprocess
from pathlib import Path

import pytest

from migration_agent.sandbox.git_tools import (
    AGENT_NAME,
    GitError,
    checkpoint,
    diff,
    init_repo,
    rollback,
)

DB = "from sqlalchemy.orm import Session\n\n\ndef names(engine):\n    return []\n"
TEST = "from app import db\n\n\ndef test_names():\n    assert db.names(None) == []\n"


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def write(root: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    write(root, {"app/__init__.py": "", "app/db.py": DB, "tests/test_db.py": TEST})
    return root


def test_init_repo_commits_every_file_and_returns_the_hash(repo: Path) -> None:
    commit = init_repo(repo)
    assert len(commit) == 40 and commit == git(repo, "rev-parse", "HEAD")
    files = git(repo, "ls-files").splitlines()
    assert files == ["app/__init__.py", "app/db.py", "tests/test_db.py"]
    assert git(repo, "status", "--porcelain") == ""


def test_checkpoint_commits_new_changed_and_deleted_files(repo: Path) -> None:
    base = init_repo(repo)
    write(repo, {"app/db.py": DB.replace("[]", "['ada']"), "app/new.py": "X = 1\n"})
    (repo / "tests" / "test_db.py").unlink()
    commit = checkpoint(str(repo), "migration: before batch 1")
    assert commit != base and commit == git(repo, "rev-parse", "HEAD")
    assert git(repo, "log", "-1", "--format=%s") == "migration: before batch 1"
    changed = git(repo, "diff", "--name-status", base, commit).splitlines()
    assert sorted(changed) == ["A\tapp/new.py", "D\ttests/test_db.py", "M\tapp/db.py"]


def test_checkpoint_without_changes_returns_the_current_commit(repo: Path) -> None:
    base = init_repo(repo)
    assert checkpoint(str(repo), "migration: before batch 1") == base
    assert git(repo, "rev-list", "--count", "HEAD") == "1"


def test_checkpoints_are_made_by_the_agent_without_hooks(repo: Path) -> None:
    init_repo(repo)
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)
    write(repo, {"app/db.py": DB + "\nX = 1\n"})
    checkpoint(str(repo), "migration: batch 1 verified")
    assert git(repo, "log", "-1", "--format=%an") == AGENT_NAME


def test_rollback_restores_edits_deletions_and_removes_new_files(repo: Path) -> None:
    base = init_repo(repo)
    write(repo, {"app/db.py": "broken(\n", "app/extra/mod.py": "Y = 2\n", "app/new.py": "Z = 3\n"})
    (repo / "tests" / "test_db.py").unlink()
    rollback(str(repo), base)
    assert (repo / "app" / "db.py").read_text(encoding="utf-8") == DB
    assert (repo / "tests" / "test_db.py").read_text(encoding="utf-8") == TEST
    assert not (repo / "app" / "new.py").exists()
    assert not (repo / "app" / "extra").exists()
    assert git(repo, "status", "--porcelain") == ""


def test_rollback_discards_commits_made_after_the_checkpoint(repo: Path) -> None:
    base = init_repo(repo)
    write(repo, {"app/db.py": DB + "\nX = 1\n"})
    checkpoint(str(repo), "migration: batch 1 verified")
    rollback(str(repo), base)
    assert git(repo, "rev-parse", "HEAD") == base
    assert (repo / "app" / "db.py").read_text(encoding="utf-8") == DB


def test_rollback_keeps_ignored_files_and_the_agent_state(repo: Path) -> None:
    write(repo, {".gitignore": "*.log\n"})
    base = init_repo(repo)
    write(repo, {"run.log": "keep me\n", ".migration_agent/graph_memory.json": "{}"})
    rollback(str(repo), base)
    assert (repo / "run.log").is_file()
    assert (repo / ".migration_agent" / "graph_memory.json").is_file()


@pytest.mark.parametrize("commit", ["", "--hard", "0" * 40, "no-such-branch"])
def test_rollback_to_something_that_is_not_a_commit_raises(repo: Path, commit: str) -> None:
    init_repo(repo)
    with pytest.raises(GitError):
        rollback(str(repo), commit)


def test_the_agent_state_folder_is_never_committed_or_diffed(repo: Path) -> None:
    base = init_repo(repo)
    write(repo, {".migration_agent/graph_memory.json": "{}"})
    checkpoint(str(repo), "migration: before batch 1")
    assert ".migration_agent" not in git(repo, "ls-files")
    assert ".migration_agent" not in diff(str(repo), base)


def test_diff_covers_committed_uncommitted_and_new_files(repo: Path) -> None:
    base = init_repo(repo)
    write(repo, {"app/db.py": DB.replace("[]", "['ada']")})
    checkpoint(str(repo), "migration: batch 1 verified")
    write(repo, {"app/new.py": "Z = 3\n", "tests/test_db.py": TEST + "\n"})
    patch = diff(str(repo), base)
    for name in ("app/db.py", "app/new.py", "tests/test_db.py"):
        assert f"diff --git a/{name} b/{name}" in patch
    assert "+    return ['ada']" in patch
    # The repository's own index is untouched: the new file is still untracked.
    assert "?? app/new.py" in git(repo, "status", "--porcelain").splitlines()


def test_diff_applies_cleanly_to_the_original_files(repo: Path, tmp_path: Path) -> None:
    base = init_repo(repo)
    write(repo, {"app/db.py": DB.replace("[]", "['ada']"), "app/new.py": "Z = 3\n"})
    (repo / "tests" / "test_db.py").unlink()
    patch = diff(str(repo), base)

    copy = tmp_path / "copy"
    write(copy, {"app/__init__.py": "", "app/db.py": DB, "tests/test_db.py": TEST})
    init_repo(copy)
    (tmp_path / "migration.patch").write_bytes(patch.encode("utf-8"))
    git(copy, "apply", str(tmp_path / "migration.patch"))
    assert (copy / "app" / "db.py").read_text(encoding="utf-8") == DB.replace("[]", "['ada']")
    assert (copy / "app" / "new.py").read_text(encoding="utf-8") == "Z = 3\n"
    assert not (copy / "tests" / "test_db.py").exists()


def test_diff_without_changes_is_empty(repo: Path) -> None:
    base = init_repo(repo)
    assert diff(str(repo), base) == ""


def test_a_folder_inside_another_repository_is_refused(tmp_path: Path) -> None:
    outer = tmp_path / "project"
    write(outer, {"README.md": "project\n", "corpus/task/before/app.py": "X = 1\n"})
    init_repo(outer)
    before = git(outer, "rev-parse", "HEAD")
    task = outer / "corpus" / "task" / "before"
    write(task, {"app.py": "X = 2\n"})
    with pytest.raises(GitError, match="not its root"):
        checkpoint(str(task), "migration: before")
    with pytest.raises(GitError, match="not its root"):
        rollback(str(task), before)
    assert git(outer, "rev-parse", "HEAD") == before


def test_checkpoint_initialises_a_folder_that_is_in_no_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    plain = tmp_path / "plain"
    write(plain, {"app.py": "X = 1\n"})
    probe = subprocess.run(
        ["git", "-C", str(plain), "rev-parse", "--show-toplevel"],
        capture_output=True,
        env=os.environ.copy(),
    )
    if probe.returncode == 0:
        pytest.skip("the temporary folder is inside a repository that cannot be hidden")
    commit = checkpoint(str(plain), "migration: before")
    assert commit == git(plain, "rev-parse", "HEAD")
    assert git(plain, "ls-files") == "app.py"


def test_a_missing_folder_raises(tmp_path: Path) -> None:
    with pytest.raises(GitError, match="does not exist"):
        checkpoint(str(tmp_path / "missing"), "migration: before")
