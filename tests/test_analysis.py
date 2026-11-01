"""Tests for Module 2, sub-components 2.1 to 2.3: scanner, parser, import graph, symbols.

Each test builds a small throwaway repository in a temporary directory.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from migration_agent.analysis.imports import ImportGraphBuilder, resolve_relative
from migration_agent.analysis.parser import ParsedModule, SourceParser, module_name
from migration_agent.analysis.scanner import RepositoryScanner
from migration_agent.analysis.symbols import SymbolTable
from migration_agent.models import ParseFailure

DECL = "sqlalchemy.ext.declarative.declarative_base"


def make_repo(root: Path, files: dict[str, str | bytes]) -> Path:
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")
    return root


def analyse(root: Path) -> tuple[dict[str, ParsedModule], list[ParseFailure]]:
    paths = RepositoryScanner(root).scan()
    return SourceParser(root).parse_all(paths)


# ------------------------------------------------------------------ 2.1 scanner
def test_scanner_finds_sources_and_skips_tool_directories(tmp_path: Path) -> None:
    make_repo(
        tmp_path,
        {
            "app/__init__.py": "",
            "app/db.py": "",
            "tool.py": "",
            ".venv/lib/x.py": "",
            "build/lib/app/db.py": "",
            "app/__pycache__/db.cpython-312.py": "",
            "pkg.egg-info/setup.py": "",
            "notes.txt": "",
        },
    )
    assert RepositoryScanner(tmp_path).scan() == ["app/__init__.py", "app/db.py", "tool.py"]


def test_scanner_applies_extra_excludes(tmp_path: Path) -> None:
    make_repo(tmp_path, {"app/db.py": "", "vendor/lib.py": "", "migrations/0001.py": ""})
    found = RepositoryScanner(tmp_path, excludes=["vendor", "migrations/*"]).scan()
    assert found == ["app/db.py"]


def test_scanner_rejects_missing_root(tmp_path: Path) -> None:
    with pytest.raises(NotADirectoryError):
        RepositoryScanner(tmp_path / "nope")


# ------------------------------------------------------------------ 2.2 parser
def test_module_names_follow_package_directories(tmp_path: Path) -> None:
    make_repo(tmp_path, {"src/app/__init__.py": "", "src/app/db/__init__.py": "", "x.py": ""})
    assert module_name("src/app/db/session.py", tmp_path) == "app.db.session"
    assert module_name("src/app/__init__.py", tmp_path) == "app"
    assert module_name("x.py", tmp_path) == "x"


def test_syntax_error_is_recorded_not_raised(tmp_path: Path) -> None:
    make_repo(
        tmp_path, {"good.py": "x = 1\n", "bad.py": "def broken(:\n", "py2.py": "print 'hi'\n"}
    )
    modules, failures = analyse(tmp_path)
    assert list(modules) == ["good.py"]
    assert sorted(f.file for f in failures) == ["bad.py", "py2.py"]
    assert all("syntax error" in f.reason for f in failures)


def test_fallback_parser_is_used_when_ast_fails(tmp_path: Path) -> None:
    import ast

    make_repo(tmp_path, {"py2.py": "print 'hi'\n"})
    parser = SourceParser(tmp_path, fallback=lambda data, path: ast.parse("pass"))
    assert isinstance(parser.parse("py2.py"), ParsedModule)


def test_encoding_cookie_is_honoured(tmp_path: Path) -> None:
    source = "# -*- coding: latin-1 -*-\nname = 'caf\xe9'\n".encode("latin-1")
    make_repo(tmp_path, {"legacy.py": source})
    modules, failures = analyse(tmp_path)
    assert not failures and "legacy.py" in modules


def test_all_names_are_read(tmp_path: Path) -> None:
    make_repo(tmp_path, {"m.py": "__all__ = ['a', 'b']\na = b = 1\n", "n.py": "x = 1\n"})
    modules, _ = analyse(tmp_path)
    assert modules["m.py"].all_names == ["a", "b"]
    assert modules["n.py"].all_names is None


# ------------------------------------------------------------------ 2.3 import graph
@pytest.mark.parametrize(
    ("module", "is_package", "level", "target", "expected"),
    [
        ("app.db.session", False, 1, "models", "app.db.models"),
        ("app.db.session", False, 2, "core", "app.core"),
        ("app.db", True, 1, "session", "app.db.session"),
        ("app.db.session", False, 1, None, "app.db"),
        ("app.db.session", False, 3, "x", None),  # beyond the top-level package
        ("app.db.session", False, 0, "sqlalchemy", "sqlalchemy"),
    ],
)
def test_resolve_relative(module, is_package, level, target, expected) -> None:
    assert resolve_relative(module, is_package, level, target) == expected


def test_import_graph_resolves_every_import_style(tmp_path: Path) -> None:
    make_repo(
        tmp_path,
        {
            "app/__init__.py": "",
            "app/models.py": "import sqlalchemy\n",
            "app/core.py": "",
            "app/util.py": "",
            "app/db/__init__.py": "",
            "app/db/session.py": (
                "from ..models import Base\n"
                "from . import engine\n"
                "import app.core\n"
                "def later():\n"
                "    from app import util\n"
                "try:\n"
                "    import ujson\n"
                "except ImportError:\n"
                "    import json\n"
            ),
            "app/db/engine.py": "from app.db.session import later\n",
        },
    )
    modules, _ = analyse(tmp_path)
    graph = ImportGraphBuilder().build(modules)
    assert graph["app/db/session.py"] == [
        "app/__init__.py",
        "app/core.py",
        "app/db/__init__.py",
        "app/db/engine.py",
        "app/models.py",
        "app/util.py",
    ]
    assert graph["app/models.py"] == []  # third-party imports are not edges
    # session and engine import each other: both edges are kept for the planner.
    assert "app/db/session.py" in graph["app/db/engine.py"]


# ------------------------------------------------------------------ 2.3 symbol table
def build_table(tmp_path: Path, files: dict[str, str]) -> SymbolTable:
    make_repo(tmp_path, files)
    modules, failures = analyse(tmp_path)
    assert not failures
    return SymbolTable.build(modules)


def test_resolves_every_spelling_of_one_api(tmp_path: Path) -> None:
    table = build_table(
        tmp_path,
        {
            "a.py": "from sqlalchemy.ext.declarative import declarative_base\n",
            "b.py": "from sqlalchemy.ext import declarative as decl\n",
            "c.py": "import sqlalchemy.ext.declarative\n",
            "d.py": "from sqlalchemy.ext.declarative import declarative_base as make_base\n",
        },
    )
    assert table.resolve("declarative_base", "a") == DECL
    assert table.resolve("decl.declarative_base", "b") == DECL
    assert table.resolve("sqlalchemy.ext.declarative.declarative_base", "c") == DECL
    assert table.resolve("make_base", "d") == DECL


def test_follows_reexport_through_package_init(tmp_path: Path) -> None:
    table = build_table(
        tmp_path,
        {
            "app/__init__.py": "",
            "app/db/__init__.py": "from .base import declarative_base\n",
            "app/db/base.py": "from sqlalchemy.ext.declarative import declarative_base\n",
            "app/models.py": "from app.db import declarative_base\nimport app.db as database\n",
        },
    )
    assert table.resolve("declarative_base", "app.models") == DECL
    assert table.resolve("database.declarative_base", "app.models") == DECL


def test_star_imports(tmp_path: Path) -> None:
    table = build_table(
        tmp_path,
        {
            "compat.py": (
                "__all__ = ['declarative_base']\n"
                "from sqlalchemy.ext.declarative import declarative_base\n"
                "from sqlalchemy import select\n"
            ),
            "public.py": "from sqlalchemy.orm import Session\n_private = 1\n",
            "user1.py": "from compat import *\n",
            "user2.py": "from public import *\n",
            "user3.py": "from sqlalchemy.orm import *\n",
        },
    )
    assert table.resolve("declarative_base", "user1") == DECL
    assert table.resolve("select", "user1") is None  # not in __all__
    assert table.resolve("Session", "user2") == "sqlalchemy.orm.Session"
    assert table.resolve("_private", "user2") is None
    assert table.resolve("Session", "user3") is None  # names of a third-party module are unknowable
    assert table.has_unresolved_star("user3") and not table.has_unresolved_star("user1")


def test_local_definitions_and_unknown_names(tmp_path: Path) -> None:
    table = build_table(tmp_path, {"app.py": "def helper():\n    pass\nVALUE = 1\n"})
    assert table.resolve("helper", "app") == "app.helper"
    assert table.resolve("VALUE", "app") == "app.VALUE"
    assert table.resolve("session.query", "app") is None  # a local variable: needs type inference


def test_reexport_cycle_terminates(tmp_path: Path) -> None:
    table = build_table(tmp_path, {"a.py": "from b import x\n", "b.py": "from a import x\n"})
    assert table.resolve("x", "a") is None
