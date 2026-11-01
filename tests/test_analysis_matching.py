"""Tests for Module 2, sub-components 2.4 to 2.7 and the StaticAnalyzer facade.

The fixtures are small SQLAlchemy 1.4 style repositories. SQLAlchemy itself is
not needed: the analysis is static.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from migration_agent.analysis import StaticAnalyzer
from migration_agent.analysis.callgraph import CallGraphBuilder
from migration_agent.analysis.impact import ImpactAnalyzer
from migration_agent.analysis.matcher import CallSiteMatcher
from migration_agent.analysis.oracle import OracleDetector, clean_code
from migration_agent.analysis.parser import SourceParser
from migration_agent.analysis.scanner import RepositoryScanner
from migration_agent.analysis.symbols import SymbolTable
from migration_agent.models import CallSite, DeprecatedPattern, MigrationSpec, UnresolvedReason

PATTERNS = [
    DeprecatedPattern("declarative_base", "sqlalchemy.ext.declarative.declarative_base"),
    DeprecatedPattern("engine_execute", "sqlalchemy.engine.Engine.execute"),
    DeprecatedPattern("session_query", "sqlalchemy.orm.Session.query"),
]


def make_repo(root: Path, files: dict[str, str]) -> Path:
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return root


def load(root: Path):
    modules, failures = SourceParser(root).parse_all(RepositoryScanner(root).scan())
    assert not failures
    return modules, SymbolTable.build(modules)


def match(root: Path, files: dict[str, str]) -> tuple[list[CallSite], CallSiteMatcher]:
    make_repo(root, files)
    modules, symbols = load(root)
    matcher = CallSiteMatcher(PATTERNS, symbols)
    return matcher.find(modules), matcher


def found(sites: list[CallSite]) -> set[tuple[str, int, str, str]]:
    return {(s.file, s.line, s.pattern_id, s.enclosing_function) for s in sites}


# ------------------------------------------------------------------ 2.4 matching
def test_module_level_function_call(tmp_path: Path) -> None:
    sites, _ = match(
        tmp_path,
        {
            "models.py": (
                "from sqlalchemy.ext.declarative import declarative_base\n"
                "Base = declarative_base()\n"
            )
        },
    )
    assert found(sites) == {("models.py", 2, "declarative_base", "models.<module>")}


def test_engine_from_module_variable_used_inside_function(tmp_path: Path) -> None:
    sites, _ = match(
        tmp_path,
        {
            "db.py": (
                "from sqlalchemy import create_engine\n"
                "engine = create_engine('sqlite://')\n"
                "def count_users():\n"
                "    return engine.execute('select count(*) from users').scalar()\n"
            )
        },
    )
    assert found(sites) == {("db.py", 4, "engine_execute", "db.count_users")}


def test_session_from_sessionmaker_factory(tmp_path: Path) -> None:
    sites, _ = match(
        tmp_path,
        {
            "repo.py": (
                "from sqlalchemy.orm import sessionmaker\n"
                "Session = sessionmaker()\n"
                "def get_user(uid):\n"
                "    session = Session()\n"
                "    return session.query(User).get(uid)\n"
                "def chained():\n"
                "    return sessionmaker()().query(User).all()\n"
            )
        },
    )
    assert found(sites) == {
        ("repo.py", 5, "session_query", "repo.get_user"),
        ("repo.py", 7, "session_query", "repo.chained"),
    }


def test_with_block_annotation_and_alias(tmp_path: Path) -> None:
    sites, _ = match(
        tmp_path,
        {
            "svc.py": (
                "from sqlalchemy.orm import Session as S\n"
                "import sqlalchemy.orm\n"
                "def a(engine):\n"
                "    with S(engine) as s:\n"
                "        return s.query(User).all()\n"
                "def b(session: S):\n"
                "    return session.query(User).count()\n"
                "def c(session: 'sqlalchemy.orm.Session'):\n"
                "    return session.query(User).first()\n"
                "def d():\n"
                "    return S().query(User).all()\n"
            )
        },
    )
    assert {(s.line, s.enclosing_function) for s in sites} == {
        (5, "svc.a"),
        (7, "svc.b"),
        (9, "svc.c"),
        (11, "svc.d"),
    }


def test_session_stored_on_self(tmp_path: Path) -> None:
    sites, _ = match(
        tmp_path,
        {
            "repo.py": (
                "from sqlalchemy.orm import Session\n"
                "class UserRepo:\n"
                "    def __init__(self, engine):\n"
                "        self.session = Session(engine)\n"
                "    def all(self):\n"
                "        return self.session.query(User).all()\n"
                "    def nested(self):\n"
                "        def inner():\n"
                "            return self.session.query(User).count()\n"
                "        return inner()\n"
            )
        },
    )
    assert {(s.line, s.enclosing_function) for s in sites} == {
        (6, "repo.UserRepo.all"),
        (9, "repo.UserRepo.nested.inner"),
    }


def test_factory_imported_from_another_file(tmp_path: Path) -> None:
    sites, _ = match(
        tmp_path,
        {
            "app/__init__.py": "",
            "app/db.py": (
                "from sqlalchemy import create_engine\n"
                "from sqlalchemy.orm import sessionmaker, scoped_session\n"
                "engine = create_engine('sqlite://')\n"
                "SessionLocal = sessionmaker(bind=engine)\n"
                "db_session = scoped_session(SessionLocal)\n"
            ),
            "app/views.py": (
                "from app.db import SessionLocal, engine, db_session\n"
                "from app import db\n"
                "def list_users():\n"
                "    session = SessionLocal()\n"
                "    return session.query(User).all()\n"
                "def raw():\n"
                "    return engine.execute('select 1')\n"
                "def via_module():\n"
                "    return db.engine.execute('select 2')\n"
                "def proxy():\n"
                "    return db_session.query(User).all()\n"
            ),
        },
    )
    assert found(sites) == {
        ("app/views.py", 5, "session_query", "app.views.list_users"),
        ("app/views.py", 7, "engine_execute", "app.views.raw"),
        ("app/views.py", 9, "engine_execute", "app.views.via_module"),
        ("app/views.py", 11, "session_query", "app.views.proxy"),
    }


def test_new_api_and_unrelated_calls_are_not_matched(tmp_path: Path) -> None:
    sites, matcher = match(
        tmp_path,
        {
            "ok.py": (
                "from sqlalchemy import create_engine, select\n"
                "from sqlalchemy.orm import Session, declarative_base\n"
                "import requests\n"
                "engine = create_engine('sqlite://')\n"
                "Base = declarative_base()\n"  # the 2.0 location: not deprecated
                "def f():\n"
                "    with engine.connect() as conn:\n"
                "        conn.execute(select(User))\n"  # Connection.execute is fine
                "    with Session(engine) as s:\n"
                "        s.execute(select(User))\n"
                "    requests.query('x')\n"
            )
        },
    )
    assert sites == [] and matcher.unresolved() == []


def test_unresolved_sites_are_reported_with_a_reason(tmp_path: Path) -> None:
    sites, matcher = match(
        tmp_path,
        {
            "dyn.py": (
                "from sqlalchemy.ext.declarative import *\n"
                "def a(s):\n"
                "    return s.query(User)\n"
                "def b(session):\n"
                "    return getattr(session, 'query')(User)\n"
                "def c():\n"
                "    return eval('session.query(User)')\n"
                "Base = declarative_base()\n"
            )
        },
    )
    assert sites == []
    assert {(u.line, u.reason) for u in matcher.unresolved()} == {
        (3, UnresolvedReason.UNKNOWN_RECEIVER),
        (5, UnresolvedReason.DYNAMIC_ATTRIBUTE),
        (7, UnresolvedReason.DYNAMIC_CALL),
        (8, UnresolvedReason.STAR_IMPORT),
    }


# ------------------------------------------------------------------ 2.5 call graph and 2.6 impact
GRAPH_REPO = {
    "app/__init__.py": "",
    "app/repo.py": (
        "from sqlalchemy.orm import Session\n"
        "class Repo:\n"
        "    def __init__(self):\n"
        "        self.session = Session()\n"
        "    def users(self):\n"
        "        return self.session.query(User).all()\n"
        "    def first(self):\n"
        "        return self.users()[0]\n"
    ),
    "app/service.py": (
        "from app.repo import Repo\n"
        "def list_users():\n"
        "    return Repo().users()\n"
        "def first_user(repo: Repo):\n"
        "    return repo.first()\n"
        "def recurse(n):\n"
        "    return recurse(n - 1) if n else list_users()\n"
    ),
    "app/main.py": (
        "from app import service\ndef main():\n    print(service.list_users())\nmain()\n"
    ),
}


def test_call_graph_links_in_repo_functions_only(tmp_path: Path) -> None:
    make_repo(tmp_path, GRAPH_REPO)
    modules, symbols = load(tmp_path)
    builder = CallGraphBuilder(symbols)
    graph = builder.build(modules)
    assert graph["app.service.list_users"] == ["app.repo.Repo.__init__", "app.repo.Repo.users"]
    assert graph["app.service.first_user"] == ["app.repo.Repo.first"]
    assert graph["app.repo.Repo.first"] == ["app.repo.Repo.users"]
    assert graph["app.service.recurse"] == ["app.service.list_users"]  # no self-loop
    assert graph["app.main.main"] == ["app.service.list_users"]  # print() is not an edge
    assert graph["app.main.<module>"] == ["app.main.main"]
    assert builder.function_files["app.repo.Repo.users"] == "app/repo.py"


def test_impact_walks_up_to_the_depth_limit(tmp_path: Path) -> None:
    make_repo(tmp_path, GRAPH_REPO)
    modules, symbols = load(tmp_path)
    sites = CallSiteMatcher(PATTERNS, symbols).find(modules)
    builder = CallGraphBuilder(symbols)
    graph = builder.build(modules)

    full = ImpactAnalyzer(depth_limit=10).compute(sites, graph, builder.function_files)
    assert full.depth == {
        "app.repo.Repo.users": 0,
        "app.repo.Repo.first": 1,
        "app.service.list_users": 1,
        "app.main.main": 2,
        "app.service.first_user": 2,
        "app.service.recurse": 2,
        "app.main.<module>": 3,
    }
    assert full.files == ["app/main.py", "app/repo.py", "app/service.py"]

    shallow = ImpactAnalyzer(depth_limit=1).compute(sites, graph, builder.function_files)
    assert max(shallow.depth.values()) == 1
    assert shallow.files == ["app/repo.py", "app/service.py"]


def test_impact_rejects_negative_depth() -> None:
    with pytest.raises(ValueError):
        ImpactAnalyzer(depth_limit=-1)


# ------------------------------------------------------------------ 2.7 oracle
def test_clean_code_blanks_comments_strings_and_imports() -> None:
    code = clean_code(
        b"from sqlalchemy.orm import query  # query\n"
        b"x = 'query'\n"
        b"y = f'{query} query'\n"
        b"session.query(User)\n"
    )
    assert code is not None
    assert code.count("query") == 2  # the f-string expression and the real call
    assert code.count("\n") == 4


def test_oracle_agrees_with_matcher_on_a_clean_repository(tmp_path: Path) -> None:
    make_repo(tmp_path, GRAPH_REPO)
    modules, symbols = load(tmp_path)
    sites = CallSiteMatcher(PATTERNS, symbols).find(modules)
    oracle = OracleDetector(PATTERNS)
    assert oracle.count(tmp_path) == len(sites) == 1


def test_oracle_ignores_files_without_the_package_and_honours_regexes(tmp_path: Path) -> None:
    make_repo(
        tmp_path,
        {
            "other.py": "def query():\n    return query_db.query()\n",  # no sqlalchemy here
            "db.py": (
                "import sqlalchemy\n"
                "engine = sqlalchemy.create_engine('sqlite://')\n"
                "engine.execute('a')\n"
                "with engine.connect() as conn:\n"
                "    conn.execute('b')\n"
            ),
        },
    )
    default = OracleDetector(PATTERNS).count_by_pattern(tmp_path)
    assert default["session_query"] == 0
    assert default["engine_execute"] == 2  # final name alone cannot tell the two apart
    tuned = OracleDetector(PATTERNS, regexes={"engine_execute": r"\bengine\.execute\("})
    assert tuned.count_by_pattern(tmp_path)["engine_execute"] == 1


# ------------------------------------------------------------------ facade
def test_static_analyzer_end_to_end(tmp_path: Path) -> None:
    make_repo(tmp_path, {**GRAPH_REPO, "broken.py": "def oops(:\n"})
    spec = MigrationSpec("sqla", "SQLAlchemy 1.4 to 2.0", "2.0", deprecated_patterns=PATTERNS)
    analyzer = StaticAnalyzer(spec, tmp_path)
    result = analyzer.analyze()
    assert [s.enclosing_function for s in result.call_sites] == ["app.repo.Repo.users"]
    assert result.import_graph["app/service.py"] == ["app/repo.py"]
    assert "app/main.py" in result.impact_set.files
    assert [f.file for f in result.parse_failures] == ["broken.py"]
    assert analyzer.baseline_count() == 1
    result.to_dict()  # the whole result serialises for the log


def test_static_analyzer_rejects_a_repository_with_nothing_parsable(tmp_path: Path) -> None:
    make_repo(tmp_path, {"broken.py": "def oops(:\n"})
    spec = MigrationSpec("sqla", "SQLAlchemy 1.4 to 2.0", "2.0", deprecated_patterns=PATTERNS)
    with pytest.raises(ValueError, match="no parsable"):
        StaticAnalyzer(spec, tmp_path).analyze()
