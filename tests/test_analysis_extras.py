"""Tests for the Python 2 fallback parser and bare references (Module 2)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from migration_agent.analysis import StaticAnalyzer
from migration_agent.analysis.callgraph import CallGraphBuilder
from migration_agent.analysis.matcher import CallSiteMatcher
from migration_agent.analysis.oracle import OracleDetector
from migration_agent.analysis.parser import ParsedModule, SourceParser
from migration_agent.analysis.py2 import AVAILABLE, python2_fallback
from migration_agent.analysis.scanner import RepositoryScanner
from migration_agent.analysis.symbols import SymbolTable
from migration_agent.models import DeprecatedPattern, MigrationSpec, ParseFailure

PATTERNS = [
    DeprecatedPattern("declarative_base", "sqlalchemy.ext.declarative.declarative_base"),
    DeprecatedPattern("session_query", "sqlalchemy.orm.Session.query"),
]
SPEC = MigrationSpec("sqla", "SQLAlchemy 1.4 to 2.0", "2.0", deprecated_patterns=PATTERNS)

needs_lib2to3 = pytest.mark.skipif(not AVAILABLE, reason="lib2to3 ships with Python 3.12 only")

PY2_SOURCE = (
    "from sqlalchemy.orm import Session\n"
    'print "loading users"\n'
    "def load(engine):\n"
    "    try:\n"
    "        session = Session(engine)\n"
    "        return session.query(User).all()\n"
    "    except ValueError, error:\n"
    "        print >>sys.stderr, error\n"
)


def make_repo(root: Path, files: dict[str, str]) -> Path:
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return root


# ------------------------------------------------------------------ Python 2 fallback
@needs_lib2to3
def test_fallback_parses_python2_and_keeps_line_numbers(tmp_path: Path) -> None:
    make_repo(tmp_path, {"legacy.py": PY2_SOURCE})
    parsed = SourceParser(tmp_path, python2_fallback).parse("legacy.py")
    assert isinstance(parsed, ParsedModule) and parsed.python2
    query_line = next(
        node.lineno
        for node in ast.walk(parsed.tree)
        if isinstance(node, ast.Attribute) and node.attr == "query"
    )
    assert query_line == 6  # same line as in the original file


@needs_lib2to3
def test_python3_files_are_not_marked_python2(tmp_path: Path) -> None:
    make_repo(tmp_path, {"modern.py": "print('hi')\n"})
    parsed = SourceParser(tmp_path, python2_fallback).parse("modern.py")
    assert isinstance(parsed, ParsedModule) and not parsed.python2


@needs_lib2to3
def test_text_that_is_neither_python3_nor_python2_still_fails(tmp_path: Path) -> None:
    make_repo(tmp_path, {"broken.py": "def oops(:\n"})
    result = SourceParser(tmp_path, python2_fallback).parse("broken.py")
    assert isinstance(result, ParseFailure) and "fallback too" in result.reason


@needs_lib2to3
def test_static_analyzer_finds_call_sites_in_python2_files(tmp_path: Path) -> None:
    make_repo(tmp_path, {"legacy.py": PY2_SOURCE})
    result = StaticAnalyzer(SPEC, tmp_path).analyze()
    assert result.parse_failures == []
    assert [(s.file, s.line, s.enclosing_function) for s in result.call_sites] == [
        ("legacy.py", 6, "legacy.load")
    ]


# ------------------------------------------------------------------ bare references
REFERENCE_REPO = {
    "models.py": (
        "from sqlalchemy.ext.declarative import declarative_base\n"
        "from sqlalchemy.orm import Session\n"
        "make_base = declarative_base\n"
        "FACTORIES = [declarative_base, dict]\n"
        "def helper():\n"
        "    return 1\n"
        "def run(engine):\n"
        "    session = Session(engine)\n"
        "    lookup = session.query\n"
        "    return lookup(User), map(helper, [])\n"
    ),
}


def load(root: Path):
    modules, failures = SourceParser(root).parse_all(RepositoryScanner(root).scan())
    assert not failures
    return modules, SymbolTable.build(modules)


def test_names_used_without_a_call_are_matched(tmp_path: Path) -> None:
    make_repo(tmp_path, REFERENCE_REPO)
    modules, symbols = load(tmp_path)
    sites = CallSiteMatcher(PATTERNS, symbols).find(modules)
    assert [(s.line, s.pattern_id, s.enclosing_function) for s in sites] == [
        (3, "declarative_base", "models.<module>"),
        (4, "declarative_base", "models.<module>"),
        (9, "session_query", "models.run"),
    ]


def test_references_do_not_create_call_graph_edges(tmp_path: Path) -> None:
    make_repo(tmp_path, REFERENCE_REPO)
    modules, symbols = load(tmp_path)
    graph = CallGraphBuilder(symbols).build(modules)
    assert graph["models.run"] == []  # helper is passed to map(), not called here


def test_oracle_counts_references_like_the_matcher(tmp_path: Path) -> None:
    make_repo(tmp_path, REFERENCE_REPO)
    modules, symbols = load(tmp_path)
    sites = CallSiteMatcher(PATTERNS, symbols).find(modules)
    assert OracleDetector(PATTERNS).count(tmp_path) == len(sites) == 3
