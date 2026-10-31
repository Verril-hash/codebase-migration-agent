"""Run the linter and tests inside a Docker container.

Owner: Prajwal (Module 6). Design: SDD section 9: network disabled, resource
limits, an isolated copy of the repository, never the working copy.
"""

from __future__ import annotations

from migration_agent.models import TestReport


def run_lint(repo_path: str, files: list[str]) -> list[str]:
    raise NotImplementedError("Module 6 (Prajwal): run_lint")


def run_tests(repo_path: str, selection: list[str] | None = None) -> TestReport:
    """Run the selected tests (all when None) and return a parsed report."""
    raise NotImplementedError("Module 6 (Prajwal): run_tests")
