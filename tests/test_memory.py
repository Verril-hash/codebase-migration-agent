"""Tests for Module 4: graph memory and the token-bounded context builder."""

import json
from pathlib import Path

import pytest

from migration_agent.analysis import StaticAnalyzer
from migration_agent.memory.context import build_context, estimate_tokens
from migration_agent.memory.graph_memory import GraphMemory, default_state_path
from migration_agent.models import (
    AnalysisResult,
    CallSite,
    DeprecatedPattern,
    FileState,
    ImpactSet,
    MigrationSpec,
    ParseFailure,
    UnresolvedReason,
    UnresolvedSite,
)

DB = "app/db.py"
SVC = "app/svc.py"
API = "app/api.py"


def make_analysis() -> AnalysisResult:
    """api -> svc -> db, one call site in db and one in svc, one unresolved call in api."""
    return AnalysisResult(
        import_graph={API: [SVC], SVC: [DB], DB: []},
        call_graph={"app.api.handle": ["app.svc.run"], "app.svc.run": ["app.db.get_user"]},
        call_sites=[
            CallSite(DB, 12, 4, "app.db.get_user", "query_api"),
            CallSite(DB, 3, 0, "app.db.setup", "engine_api"),
            CallSite(SVC, 7, 8, "app.svc.run", "query_api"),
        ],
        impact_set=ImpactSet(files=[DB, SVC, API]),
        unresolved_sites=[UnresolvedSite(API, 5, "getattr(s, m)()", UnresolvedReason.DYNAMIC_CALL)],
    )


def write_repo(root: Path) -> None:
    (root / "app").mkdir()
    (root / DB).write_text("\n".join(f"line {i}" for i in range(1, 101)) + "\n")
    (root / SVC).write_text("import app.db\n\ndef run():\n    pass\n")
    (root / API).write_text("def handle():\n    pass\n")


# ----------------------------------------------------------------- GraphMemory
def test_from_analysis_builds_one_node_per_file() -> None:
    memory = GraphMemory.from_analysis(make_analysis())
    assert memory.paths() == [API, DB, SVC]
    assert all(node.state is FileState.ANALYSED for node in memory)


def test_dependencies_and_dependents_are_mirrored() -> None:
    memory = GraphMemory.from_analysis(make_analysis())
    assert memory.get(API).dependencies == [SVC]
    assert memory.get(SVC).dependencies == [DB]
    assert memory.get(SVC).dependents == [API]
    assert memory.get(DB).dependents == [SVC]
    assert memory.neighbours(SVC) == [API, DB]


def test_call_graph_adds_file_dependencies_without_import() -> None:
    analysis = AnalysisResult(
        import_graph={DB: [], SVC: []},
        call_graph={"app.svc.run": ["app.db.get_user"]},
    )
    memory = GraphMemory.from_analysis(analysis)
    assert memory.get(SVC).dependencies == [DB]
    assert memory.get(DB).dependents == [SVC]


def test_call_sites_are_attached_and_ordered_by_line() -> None:
    memory = GraphMemory.from_analysis(make_analysis())
    assert [site.line for site in memory.get(DB).call_sites] == [3, 12]
    assert memory.get(API).call_sites == []


def test_unresolved_sites_and_parse_failures_are_kept() -> None:
    analysis = make_analysis()
    analysis.parse_failures = [ParseFailure("app/broken.py", "invalid syntax")]
    memory = GraphMemory.from_analysis(analysis)
    assert memory.unresolved[API][0].reason is UnresolvedReason.DYNAMIC_CALL
    assert memory.get("app/broken.py").state is FileState.FAILED
    assert memory.parse_failures[0].reason == "invalid syntax"


def test_get_unknown_file_names_the_path() -> None:
    memory = GraphMemory.from_analysis(make_analysis())
    with pytest.raises(KeyError, match="nope.py"):
        memory.get("nope.py")


def test_update_state_keeps_call_site_status_in_step() -> None:
    memory = GraphMemory.from_analysis(make_analysis())
    memory.update_state(DB, FileState.EDITED)
    assert {s.status for s in memory.get(DB).call_sites} == {"patched"}
    memory.update_state(DB, FileState.VERIFIED)
    assert {s.status for s in memory.get(DB).call_sites} == {"verified"}
    memory.update_state(DB, FileState.ROLLED_BACK)
    assert {s.status for s in memory.get(DB).call_sites} == {"pending"}
    assert memory.get_state(DB) is FileState.ROLLED_BACK


def test_verified_clears_fixes_and_writes_a_summary() -> None:
    memory = GraphMemory.from_analysis(make_analysis())
    memory.add_pending_fix(DB, "fix-1")
    memory.record_retry(DB)
    memory.update_state(DB, FileState.VERIFIED)
    node = memory.get(DB)
    assert node.pending_fixes == []
    assert "2 call site(s)" in node.summary and "1 repair attempt" in node.summary


def test_pending_fixes_are_unique_and_removable() -> None:
    memory = GraphMemory.from_analysis(make_analysis())
    memory.add_pending_fix(DB, "fix-1")
    memory.add_pending_fix(DB, "fix-1")
    assert memory.open_failures(DB) == ["fix-1"]
    memory.resolve_fix(DB, "fix-1")
    memory.resolve_fix(DB, "never-added")
    assert memory.open_failures(DB) == []


def test_record_retry_counts_up() -> None:
    memory = GraphMemory.from_analysis(make_analysis())
    assert memory.record_retry(SVC) == 1
    assert memory.record_retry(SVC) == 2
    assert memory.get(SVC).retry_count == 2


def test_summary_is_cut_to_the_limit() -> None:
    memory = GraphMemory.from_analysis(make_analysis())
    memory.set_summary(DB, "word " * 200)
    assert len(memory.get(DB).summary) <= 240
    assert memory.get(DB).summary.endswith("…")


def test_counts_and_status_lines() -> None:
    memory = GraphMemory.from_analysis(make_analysis())
    memory.update_state(DB, FileState.VERIFIED)
    counts = memory.counts()
    assert counts["analysed"] == 2 and counts["verified"] == 1 and counts["failed"] == 0
    assert memory.in_state(FileState.VERIFIED) == [DB]
    assert memory.status_lines()[1].split() == ["verified", DB]


def test_save_and_load_round_trip(tmp_path: Path) -> None:
    memory = GraphMemory.from_analysis(make_analysis())
    memory.update_state(SVC, FileState.EDITED)
    memory.add_pending_fix(SVC, "fix-9")
    memory.set_summary(DB, "done")
    target = tmp_path / "state" / "memory.json"
    memory.save(target)
    assert not target.with_name("memory.json.tmp").exists()
    restored = GraphMemory.load(target)
    assert restored.to_dict() == memory.to_dict()
    assert restored.get(SVC).state is FileState.EDITED
    assert restored.get(SVC).call_sites[0].status == "patched"
    assert restored.unresolved[API][0].reason is UnresolvedReason.DYNAMIC_CALL


def test_saved_file_is_plain_json(tmp_path: Path) -> None:
    memory = GraphMemory.from_analysis(make_analysis())
    memory.save(tmp_path / "m.json")
    data = json.loads((tmp_path / "m.json").read_text(encoding="utf-8"))
    assert data["version"] == 1 and len(data["nodes"]) == 3


def test_load_rejects_another_schema_version(tmp_path: Path) -> None:
    (tmp_path / "m.json").write_text('{"version": 99, "nodes": []}', encoding="utf-8")
    with pytest.raises(ValueError, match="version"):
        GraphMemory.load(tmp_path / "m.json")


def test_default_state_path_is_inside_the_repo() -> None:
    assert default_state_path("repo") == Path("repo/.migration_agent/graph_memory.json")


def test_content_hash_detects_drift(tmp_path: Path) -> None:
    write_repo(tmp_path)
    memory = GraphMemory.from_analysis(make_analysis(), tmp_path)
    memory.refresh_hashes()
    assert memory.get(DB).content_hash and memory.drifted() == []
    (tmp_path / DB).write_text("changed\n")
    assert memory.drifted() == [DB]
    memory.refresh_hashes([DB])
    assert memory.drifted() == []


def test_drift_without_repo_root_reports_nothing() -> None:
    memory = GraphMemory.from_analysis(make_analysis())
    memory.refresh_hashes()
    assert memory.get(DB).content_hash == "" and memory.drifted() == []


# ----------------------------------------------------------------- build_context
def test_context_has_target_neighbours_and_failures(tmp_path: Path) -> None:
    write_repo(tmp_path)
    memory = GraphMemory.from_analysis(make_analysis(), tmp_path)
    memory.add_pending_fix(SVC, "test_run failed: AttributeError")
    memory.set_summary(DB, "get_user migrated")
    memory.update_state(DB, FileState.VERIFIED)
    text = build_context(memory, SVC, 2000)
    assert f"Target file: {SVC}" in text
    assert "line 7, col 8: query_api in app.svc.run" in text
    assert "test_run failed: AttributeError" in text
    assert "def run():" in text
    assert f"{DB} (dependency, verified" in text and "get_user migrated" in text
    assert f"{API} (dependent, analysed" in text


def test_context_lists_unresolved_calls(tmp_path: Path) -> None:
    write_repo(tmp_path)
    memory = GraphMemory.from_analysis(make_analysis(), tmp_path)
    text = build_context(memory, API, 2000)
    assert "getattr(s, m)()" in text and "dynamic_call" in text


def test_context_without_repo_root_says_source_is_missing() -> None:
    memory = GraphMemory.from_analysis(make_analysis())
    assert "source not available" in build_context(memory, DB, 2000)


def test_context_includes_unrelated_completed_work(tmp_path: Path) -> None:
    analysis = make_analysis()
    analysis.import_graph["app/other.py"] = []
    write_repo(tmp_path)
    memory = GraphMemory.from_analysis(analysis, tmp_path)
    memory.set_summary("app/other.py", "all done")
    memory.update_state("app/other.py", FileState.VERIFIED)
    text = build_context(memory, API, 2000)
    assert "## Completed work" in text and "app/other.py: all done" in text


@pytest.mark.parametrize("budget", [20, 60, 120, 300, 1000])
def test_context_never_exceeds_the_budget(tmp_path: Path, budget: int) -> None:
    write_repo(tmp_path)
    memory = GraphMemory.from_analysis(make_analysis(), tmp_path)
    for path in (DB, SVC, API):
        assert estimate_tokens(build_context(memory, path, budget)) <= budget


def test_long_source_is_windowed_around_call_sites(tmp_path: Path) -> None:
    write_repo(tmp_path)
    analysis = make_analysis()
    analysis.call_sites = [
        CallSite(DB, 3, 0, "app.db.setup", "engine_api"),
        CallSite(DB, 90, 4, "app.db.get_user", "query_api"),
    ]
    memory = GraphMemory.from_analysis(analysis, tmp_path)
    text = build_context(memory, DB, 160)
    assert "    3  line 3" in text and "   90  line 90" in text
    assert "   50  line 50" not in text and "line 100" not in text
    assert "\n...\n" in text


def test_small_budget_keeps_the_header() -> None:
    memory = GraphMemory.from_analysis(make_analysis())
    text = build_context(memory, DB, 15)
    assert text.startswith("## Target file")
    assert estimate_tokens(text) <= 15


def test_unknown_file_raises() -> None:
    memory = GraphMemory.from_analysis(make_analysis())
    with pytest.raises(KeyError):
        build_context(memory, "missing.py", 100)


# ----------------------------------------------------------------- package layouts
def test_call_graph_edges_follow_module_two_names_in_a_src_layout(tmp_path: Path) -> None:
    for name in ("src/app/__init__.py", "src/app/db.py", "src/app/svc.py"):
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text("")
    analysis = AnalysisResult(
        import_graph={"src/app/db.py": [], "src/app/svc.py": []},
        call_graph={"app.svc.run": ["app.db.get_user"]},
    )
    memory = GraphMemory.from_analysis(analysis, tmp_path)
    assert memory.get("src/app/svc.py").dependencies == ["src/app/db.py"]
    assert memory.get("src/app/db.py").dependents == ["src/app/svc.py"]


# ----------------------------------------------------------------- with the real analyzer
def test_memory_from_a_real_static_analysis(tmp_path: Path) -> None:
    files = {
        "app/__init__.py": "",
        "app/db.py": (
            "from sqlalchemy.orm import Session\n\n"
            "def get_user(session: Session, uid):\n"
            "    return session.query(User).get(uid)\n"
        ),
        "app/svc.py": (
            "from app.db import get_user\n\ndef run(session):\n    return get_user(session, 1)\n"
        ),
        "app/broken.py": "def oops(:\n",
    }
    for relative, content in files.items():
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    spec = MigrationSpec(
        "t", "t", "2.0", [DeprecatedPattern("session_query", "sqlalchemy.orm.Session.query")]
    )
    analysis = StaticAnalyzer(spec, tmp_path).analyze()
    memory = GraphMemory.from_analysis(analysis, tmp_path)
    assert memory.get("app/svc.py").dependencies == ["app/db.py"]
    assert memory.get("app/db.py").dependents == ["app/svc.py"]
    assert [s.pattern_id for s in memory.get("app/db.py").call_sites] == ["session_query"]
    assert memory.get("app/broken.py").state is FileState.FAILED
    memory.refresh_hashes()
    assert memory.drifted() == []
    assert "session.query" in build_context(memory, "app/db.py", 500)
