"""Run the linter and tests inside a Docker container.

Owner: Prajwal (Module 6). Design: SDD section 9: network disabled, resource
limits, an isolated copy of the repository, never the working copy.

Both functions copy the repository into a fresh container
(:func:`~migration_agent.sandbox.container.run_in_container`), run one tool there
and parse the report it leaves. The image is ``SANDBOX_IMAGE`` or the default
profile's (see :mod:`migration_agent.sandbox.image`), and ``SANDBOX_TIMEOUT``
bounds a run in seconds. Paths in results are repository-relative.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from xml.etree import ElementTree

from migration_agent.models import TestFailure, TestReport
from migration_agent.sandbox.container import (
    WORKDIR,
    Limits,
    SandboxError,
    run_in_container,
)
from migration_agent.sandbox.image import default_image

#: Where pytest writes its JUnit report inside the container, outside the repository copy.
JUNIT_PATH = "/tmp/sandbox-junit.xml"
#: Lint rules that mean the code is broken rather than untidy: syntax errors, undefined
#: names and invalid comparisons. The repository's own lint configuration is ignored.
LINT_RULES = "E9,F63,F7,F82"
MESSAGE_LIMIT = 2_000
#: Characters of traceback kept, from the end, where the failing frame is.
TRACEBACK_LIMIT = 20_000
#: pytest exit codes after which its report is complete: passed, failed,
#: interrupted (for example by a collection error) and no tests collected.
_REPORTED = {0, 1, 2, 5}

_SANDBOX_PREFIX = re.compile(rf"(?<![\w.]){re.escape(WORKDIR)}/")
_FINDING = re.compile(r"^(?P<path>[^\s:][^:]*):(?P<line>\d+):(?P<column>\d+): (?P<message>.+)$")


def run_lint(repo_path: str, files: list[str]) -> list[str]:
    """Lint ``files`` (the whole repository when empty) for errors that break the code.

    Return one ``path:line:column: CODE message`` line per finding; empty means clean.
    """
    targets = _relative_paths(files, "lint target") or ["."]
    limits = Limits.from_env()
    command = [
        "ruff",
        "check",
        "--isolated",
        "--no-cache",
        "--no-fix",
        "--output-format=concise",
        f"--select={LINT_RULES}",
        *targets,
    ]
    result = run_in_container(repo_path, command, default_image(), limits)
    if result.timed_out:
        raise SandboxError(f"the linter took longer than {limits.timeout:g} s")
    if result.exit_code not in (0, 1):
        raise SandboxError(f"the linter failed (exit code {result.exit_code}): {result.output}")
    return parse_ruff(result.output)


def run_tests(
    repo_path: str,
    selection: list[str] | None = None,
    *,
    image: str | None = None,
    limits: Limits | None = None,
) -> TestReport:
    """Run the selected tests (all when None) and return a parsed report.

    ``selection`` holds pytest node ids or paths, repository-relative; an empty list
    runs everything too. A run that cannot finish (timeout, crash, no report) comes
    back as one error, never as green. Raise
    :class:`~migration_agent.sandbox.container.SandboxError` when Docker or the image
    is missing.
    """
    limits = limits or Limits.from_env()
    command = [
        "python",
        "-m",
        "pytest",
        "-q",
        "-p",
        "no:cacheprovider",
        "--color=no",
        "--tb=native",
        "-o",
        "junit_family=xunit1",
        f"--junitxml={JUNIT_PATH}",
        *_relative_paths(selection or [], "test selection"),
    ]
    result = run_in_container(
        repo_path, command, image or default_image(), limits, collect=[JUNIT_PATH]
    )
    if result.timed_out:
        return _broken(
            "sandbox::timeout",
            f"the test suite took longer than {limits.timeout:g} s and was stopped",
            result.output,
        )
    return report_from_run(result.exit_code, result.files.get(JUNIT_PATH), result.output)


def report_from_run(exit_code: int | None, junit: bytes | None, output: str) -> TestReport:
    """Turn pytest's exit code, JUnit report and console output into a TestReport.

    The exit code is checked against the report, so a report that claims success
    after pytest failed is not believed.
    """
    if junit is None:
        return _broken("sandbox::pytest", f"pytest wrote no report (exit code {exit_code})", output)
    try:
        report = parse_junit(junit)
    except ValueError as exc:
        return _broken("sandbox::pytest", f"unreadable pytest report: {exc}", output)
    if exit_code not in _REPORTED:
        problem = f"pytest stopped abnormally (exit code {exit_code})"
    elif exit_code in (1, 2) and report.green:
        problem = f"pytest exited with code {exit_code} but its report lists no failure"
    else:
        return report
    report.errors += 1
    report.failures.append(TestFailure("sandbox::pytest", problem, _tail(_clean(output))))
    return report


def parse_junit(data: bytes) -> TestReport:
    """Count the outcomes in a pytest JUnit (xunit1) report and keep every failure.

    Raise ``ValueError`` when the document cannot be read.
    """
    if b"<!DOCTYPE" in data or b"<!ENTITY" in data:
        raise ValueError("document type declarations are not allowed")
    try:
        root = ElementTree.fromstring(data)
    except ElementTree.ParseError as exc:
        raise ValueError(str(exc)) from None
    report = TestReport()
    for case in root.iter("testcase"):
        failure = case.find("failure")
        error = case.find("error")
        outcome = failure if failure is not None else error
        if outcome is None:
            if case.find("skipped") is not None:
                report.skipped += 1
            else:
                report.passed += 1
            continue
        if outcome is failure:
            report.failed += 1
        else:
            report.errors += 1
        traceback = _tail(_clean(outcome.text or ""))
        message = _clean(outcome.get("message", "")).strip()
        if not message:
            message = traceback.strip().splitlines()[-1] if traceback.strip() else outcome.tag
        report.failures.append(TestFailure(node_id(case), message[:MESSAGE_LIMIT], traceback))
    return report


def node_id(case: ElementTree.Element) -> str:
    """The pytest node id of a JUnit test case, e.g. ``tests/test_db.py::TestX::test_y``."""
    name = case.get("name", "")
    classname = case.get("classname", "")
    file = _clean(case.get("file", "")).replace("\\", "/")
    if not file.endswith(".py"):
        return f"{classname}::{name}" if classname else name
    module = file[:-3].replace("/", ".")
    if not classname and name == module:  # a module that failed to collect
        return file
    if classname == module:
        return f"{file}::{name}"
    if classname.startswith(module + "."):
        return "::".join([file, *classname[len(module) + 1 :].split("."), name])
    return f"{classname}::{name}" if classname else f"{file}::{name}"


def parse_ruff(output: str) -> list[str]:
    """Finding lines from ``ruff check --output-format=concise``; summary lines are dropped."""
    findings = []
    for line in output.splitlines():
        line = _clean(line.strip())
        if _FINDING.match(line):
            findings.append(line)
    return findings


def _relative_paths(items: list[str], what: str) -> list[str]:
    """Check that every item is a repository-relative path, not an option or an escape."""
    cleaned = []
    for item in items:
        text = str(item).replace("\\", "/")
        path = text.split("::", 1)[0]
        if (
            not path
            or text.startswith("-")
            or "\x00" in text
            or path.startswith("/")
            or re.match(r"^[A-Za-z]:", path)
            or ".." in PurePosixPath(path).parts
        ):
            raise ValueError(f"{what} must be a repository-relative path, got {item!r}")
        cleaned.append(text)
    return cleaned


def _clean(text: str) -> str:
    """Make sandbox paths repository-relative: ``/work/app/db.py`` becomes ``app/db.py``."""
    return _SANDBOX_PREFIX.sub("", text)


def _tail(text: str) -> str:
    return text[-TRACEBACK_LIMIT:]


def _broken(test: str, message: str, output: str) -> TestReport:
    return TestReport(errors=1, failures=[TestFailure(test, message, _tail(_clean(output)))])
