"""Tests for Module 8 trajectory logging and patch generation."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from migration_agent.models import FileState, LogEntry
from migration_agent.trajectory.log import SCHEMA_VERSION, TrajectoryLog, read_log, to_jsonable
from migration_agent.trajectory.patch import PatchError, emit_patch


def entry(step: int, **changes: object) -> LogEntry:
    """Create a deterministic test event."""
    values: dict[str, object] = {
        "step_id": step,
        "timestamp": "2026-11-07T10:00:00+00:00",
        "module": "editing",
    }
    values.update(action="edited", **changes)
    return LogEntry(**values)


def test_save_writes_one_json_document(tmp_path: Path) -> None:
    log = TrajectoryLog()
    log.log_event(entry(1, batch_id=0, files=["a.py"], tokens_in=10, tokens_out=4))
    log.log_event(entry(2, status="failed", detail={"reason": "oscillating"}))
    target = tmp_path / "run" / "trajectory.json"
    log.save(target)

    document = read_log(target)
    assert document["schema_version"] == SCHEMA_VERSION
    assert document["entry_count"] == 2
    first, second = document["entries"]
    assert first["step_id"] == 1 and first["files"] == ["a.py"]
    assert second["status"] == "failed" and second["batch_id"] is None
    assert not target.with_name("trajectory.json.tmp").exists()


def test_an_entry_is_a_snapshot_of_the_moment_it_was_logged() -> None:
    files = ["a.py"]
    log = TrajectoryLog()
    log.log_event(entry(1, files=files))
    files.append("changed_later.py")
    assert log.entries[0]["files"] == ["a.py"]
    log.entries[0]["files"].append("x")
    assert log.entries[0]["files"] == ["a.py"]


def test_values_that_json_cannot_hold_are_converted() -> None:
    log = TrajectoryLog()
    log.log_event(
        entry(
            1,
            detail={
                "state": FileState.EDITED,
                "path": Path("src") / "app.py",
                "seen": {"b.py", "a.py"},
                "ratio": float("nan"),
            },
        )
    )
    assert log.entries[0]["detail"] == {
        "state": "edited",
        "path": "src/app.py",
        "seen": ["a.py", "b.py"],
        "ratio": "nan",
    }
    json.dumps(log.entries, allow_nan=False)


def test_log_event_rejects_anything_but_a_log_entry() -> None:
    with pytest.raises(TypeError):
        TrajectoryLog().log_event({"step_id": 1})  # type: ignore[arg-type]


def test_totals_add_up_steps_and_tokens() -> None:
    log = TrajectoryLog()
    log.log_event(entry(1, tokens_in=10, tokens_out=2))
    log.log_event(entry(2, tokens_in=5, tokens_out=1))
    assert log.totals() == {"steps": 2, "tokens_in": 15, "tokens_out": 3}
    assert len(log) == 2


def test_live_file_gets_one_line_per_event_and_starts_empty(tmp_path: Path) -> None:
    live = tmp_path / "live.jsonl"
    live.write_text("leftover from an older run\n", encoding="utf-8")
    log = TrajectoryLog(live_path=live)
    assert live.read_text(encoding="utf-8") == ""
    log.log_event(entry(1))
    log.log_event(entry(2))
    lines = live.read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["step_id"] for line in lines] == [1, 2]


def test_read_log_rejects_a_file_of_another_schema(tmp_path: Path) -> None:
    other = tmp_path / "other.json"
    other.write_text(json.dumps({"schema_version": 99, "entries": []}), encoding="utf-8")
    with pytest.raises(ValueError):
        read_log(other)


def test_saving_twice_replaces_the_file(tmp_path: Path) -> None:
    log = TrajectoryLog()
    target = tmp_path / "t.json"
    log.log_event(entry(1))
    log.save(target)
    log.log_event(entry(2))
    log.save(target)
    assert read_log(target)["entry_count"] == 2


def test_to_jsonable_keeps_plain_values_unchanged() -> None:
    assert to_jsonable({"a": [1, 2.5, None, True, "x"]}) == {"a": [1, 2.5, None, True, "x"]}


needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


def git(cwd: Path, *args: str) -> str:
    """Run Git for a temporary test repository."""
    result = subprocess.run(
        [
            "git",
            "-c",
            "user.name=test",
            "-c",
            "user.email=test@example.com",
            "-c",
            "core.autocrlf=false",
            *args,
        ],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """Create a temporary repository with tracked and ignored files."""
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init")
    (root / "app.py").write_text("x = 1\n", encoding="utf-8", newline="\n")
    (root / "old.py").write_text("print('old')\n", encoding="utf-8", newline="\n")
    (root / ".gitignore").write_text("__pycache__/\n", encoding="utf-8", newline="\n")
    git(root, "add", "--all")
    git(root, "commit", "-m", "base")
    return root


@needs_git
def test_patch_replays_edits_new_deleted_and_binary_files(repo: Path, tmp_path: Path) -> None:
    base = git(repo, "rev-parse", "HEAD")
    (repo / "app.py").write_text("x = 2\n", encoding="utf-8", newline="\n")
    (repo / "new.py").write_text("y = 3\n", encoding="utf-8", newline="\n")
    (repo / "data.bin").write_bytes(bytes(range(256)))
    (repo / "old.py").unlink()
    (repo / "__pycache__").mkdir()
    (repo / "__pycache__" / "junk.pyc").write_bytes(b"ignored")

    patch = emit_patch(str(repo), base, tmp_path / "out" / "migration.patch")
    text = patch.read_bytes().decode("utf-8", errors="replace")
    assert "+x = 2" in text and "new file mode" in text and "deleted file mode" in text
    assert "junk.pyc" not in text

    clone = tmp_path / "clone"
    git(tmp_path, "clone", str(repo), str(clone))
    git(clone, "apply", "--check", str(patch))
    git(clone, "apply", str(patch))
    assert (clone / "app.py").read_text(encoding="utf-8") == "x = 2\n"
    assert (clone / "new.py").read_text(encoding="utf-8") == "y = 3\n"
    assert (clone / "data.bin").read_bytes() == bytes(range(256))
    assert not (clone / "old.py").exists()


@needs_git
def test_patch_includes_work_committed_after_the_base(repo: Path, tmp_path: Path) -> None:
    base = git(repo, "rev-parse", "HEAD")
    (repo / "app.py").write_text("x = 5\n", encoding="utf-8", newline="\n")
    git(repo, "commit", "-am", "checkpoint")
    (repo / "extra.py").write_text("z = 1\n", encoding="utf-8", newline="\n")

    patch = emit_patch(str(repo), base, tmp_path / "m.patch")
    text = patch.read_text(encoding="utf-8")
    assert "+x = 5" in text and "extra.py" in text


@needs_git
def test_emitting_a_patch_does_not_touch_the_repository(repo: Path, tmp_path: Path) -> None:
    base = git(repo, "rev-parse", "HEAD")
    (repo / "new.py").write_text("y = 3\n", encoding="utf-8", newline="\n")
    before = git(repo, "status", "--porcelain")
    emit_patch(str(repo), base, tmp_path / "m.patch")
    assert git(repo, "status", "--porcelain") == before == "?? new.py"


@needs_git
def test_unchanged_repository_gives_an_empty_patch(repo: Path, tmp_path: Path) -> None:
    patch = emit_patch(str(repo), git(repo, "rev-parse", "HEAD"), tmp_path / "m.patch")
    assert patch.read_bytes() == b""


@needs_git
def test_bad_inputs_raise_a_clear_error(repo: Path, tmp_path: Path) -> None:
    with pytest.raises(PatchError):
        emit_patch(str(repo), "deadbeef" * 5, tmp_path / "m.patch")
    with pytest.raises(PatchError):
        emit_patch(str(repo), "--output=evil", tmp_path / "m.patch")
    with pytest.raises(PatchError):
        emit_patch(str(tmp_path / "missing"), "HEAD", tmp_path / "m.patch")
