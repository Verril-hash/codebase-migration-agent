"""Checkpoint, diff and roll back batches with Git.

Owner: Prajwal (Module 6).
"""

from __future__ import annotations


def checkpoint(repo_path: str, message: str) -> str:
    """Commit the current state and return the commit hash."""
    raise NotImplementedError("Module 6 (Prajwal): checkpoint")


def rollback(repo_path: str, commit: str) -> None:
    raise NotImplementedError("Module 6 (Prajwal): rollback")


def diff(repo_path: str, base: str) -> str:
    raise NotImplementedError("Module 6 (Prajwal): diff")
