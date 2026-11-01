"""Tests for the oracle's counting rules and gates, and its counts on the corpus tasks."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from migration_agent.analysis.oracle import OracleDetector, default_rule
from migration_agent.models import DeprecatedPattern

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "corpus"
SPEC = json.loads((ROOT / "specs" / "sqlalchemy_1_4_to_2_0.json").read_text(encoding="utf-8"))
SPEC_PATTERNS = [
    DeprecatedPattern(p["pattern_id"], p["ast_matcher"]) for p in SPEC["deprecated_patterns"]
]
QUERY = [DeprecatedPattern("session_query", "sqlalchemy.orm.Session.query")]
DECL = [DeprecatedPattern("base", "sqlalchemy.ext.declarative.declarative_base")]


def make_repo(root: Path, files: dict[str, str]) -> Path:
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return root


# ------------------------------------------------------------------ rules
def test_default_rule_depends_on_the_kind_of_pattern() -> None:
    gate, count = default_rule("sqlalchemy.orm.Session.query")
    assert gate == r"\bsqlalchemy\b" and count == r"\.query\b"
    gate, count = default_rule("sqlalchemy.ext.declarative.declarative_base")
    assert r"sqlalchemy\.ext\.declarative" in gate and "declarative_base" in count


def test_a_variable_named_like_a_method_is_not_counted(tmp_path: Path) -> None:
    make_repo(
        tmp_path,
        {"q.py": "import sqlalchemy\nquery = build()\nsession.scalar(query)\nsession.query(X)\n"},
    )
    assert OracleDetector(QUERY).count(tmp_path) == 1


def test_a_moved_function_stops_counting_once_imported_from_the_new_place(tmp_path: Path) -> None:
    old = make_repo(
        tmp_path / "old",
        {
            "a.py": (
                "from sqlalchemy.ext.declarative import declarative_base\nB = declarative_base()\n"
            ),
            "b.py": "from sqlalchemy.ext import declarative as d\nC = d.declarative_base()\n",
        },
    )
    new = make_repo(
        tmp_path / "new",
        {"a.py": "from sqlalchemy.orm import declarative_base\nB = declarative_base()\n"},
    )
    assert OracleDetector(DECL).count(old) == 2
    assert OracleDetector(DECL).count(new) == 0


# ------------------------------------------------------------------ gates
def test_files_using_sqlalchemy_through_another_module_are_counted(tmp_path: Path) -> None:
    make_repo(
        tmp_path,
        {
            "src/app/__init__.py": "",
            "src/app/db.py": "from sqlalchemy.orm import sessionmaker\nmake = sessionmaker()\n",
            "src/app/users.py": "from app.db import make\nmake().query(User)\n",
            "src/app/views.py": "from . import users\nfrom .db import make\ns.query(X)\n",
            # reaches SQLAlchemy only through views.py, two hops away
            "src/app/deep.py": "import app.views\nq.query(Y)\n",
            # no SQLAlchemy at all
            "src/other/search.py": "def query():\n    return index.query()\n",
        },
    )
    assert OracleDetector(QUERY).count(tmp_path) == 3


def test_parenthesised_and_comma_imports_are_followed(tmp_path: Path) -> None:
    make_repo(
        tmp_path,
        {
            "db.py": "import sqlalchemy\n",
            "a.py": "import os, db\nx.query(1)\n",
            "b.py": "from db import (\n    engine,\n)\ny.query(2)\n",
        },
    )
    assert OracleDetector(QUERY).count(tmp_path) == 2


def test_a_file_that_imports_nothing_from_the_package_is_ignored(tmp_path: Path) -> None:
    make_repo(tmp_path, {"lib.py": "import requests\nrequests.query('x')\n"})
    assert OracleDetector(QUERY).count(tmp_path) == 0


# ------------------------------------------------------------------ corpus
@pytest.mark.parametrize("task", sorted(p.name for p in CORPUS.glob("verril_*")))
def test_oracle_counts_ground_truth_before_and_zero_after(task: str) -> None:
    meta = json.loads((CORPUS / task / "task.json").read_text(encoding="utf-8"))
    oracle = OracleDetector(SPEC_PATTERNS, SPEC.get("oracle_regexes"), excludes=["tests"])
    assert oracle.count(CORPUS / task / "before") == len(meta["expected_call_sites"])
    assert oracle.count(CORPUS / task / "expected") == 0
