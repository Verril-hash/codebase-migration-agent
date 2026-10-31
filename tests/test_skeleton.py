"""Smoke tests for the skeleton: every module imports and the shared shapes behave.

Each owner adds their own test file (test_analysis.py, test_planner.py, ...)
next to this one.
"""

import importlib
import json

import pytest

from migration_agent.cli import build_parser
from migration_agent.models import (
    AnalysisResult,
    CallSite,
    FileNode,
    FileState,
    MigrationSpec,
    TestFailure,
    TestReport,
)

MODULES = [
    "migration_agent.agent",
    "migration_agent.cli",
    "migration_agent.spec.loader",
    "migration_agent.analysis",
    "migration_agent.planning.planner",
    "migration_agent.memory.graph_memory",
    "migration_agent.memory.context",
    "migration_agent.editing.engine",
    "migration_agent.sandbox.runner",
    "migration_agent.sandbox.git_tools",
    "migration_agent.correction.diagnose",
    "migration_agent.llm.client",
    "migration_agent.trajectory.log",
    "migration_agent.trajectory.patch",
    "migration_agent.benchmark.harness",
    "migration_agent.benchmark.metrics",
]


@pytest.mark.parametrize("name", MODULES)
def test_module_imports(name: str) -> None:
    importlib.import_module(name)


def test_shapes_serialise_to_json() -> None:
    site = CallSite("app/db.py", 12, 4, "app.db.get_user", "query_api")
    node = FileNode("app/db.py", call_sites=[site])
    result = AnalysisResult(call_sites=[site])
    for shape in (site, node, result):
        json.dumps(shape.to_dict())
    assert node.to_dict()["state"] == "pending"


def test_file_states_match_the_design() -> None:
    assert [s.value for s in FileState] == [
        "pending",
        "analysed",
        "edited",
        "verified",
        "failed",
        "rolled_back",
    ]


def test_report_is_green_only_without_failures_or_errors() -> None:
    assert TestReport(passed=3).green
    assert not TestReport(passed=3, failed=1, failures=[TestFailure("t", "boom")]).green
    assert not TestReport(errors=1).green


def test_spec_defaults() -> None:
    spec = MigrationSpec("sqla-2", "SQLAlchemy 1.4 to 2.0", "2.0")
    assert spec.deprecated_patterns == [] and spec.replacement_rules == []


def test_cli_parses_every_command() -> None:
    parser = build_parser()
    assert parser.parse_args(["plan", "--spec", "s.json", "--repo", "r"]).command == "plan"
    assert parser.parse_args(["run", "--spec", "s.json", "--repo", "r", "--resume"]).resume
    assert parser.parse_args(["status", "--repo", "r"]).command == "status"
    assert parser.parse_args(["evaluate"]).tasks == "corpus"
