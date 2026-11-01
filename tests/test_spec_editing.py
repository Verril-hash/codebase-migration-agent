"""Tests for the specification loader, CLI, and deterministic edit engine."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from migration_agent.cli import main
from migration_agent.editing.engine import apply_codemod, validate
from migration_agent.models import Patch, ReplacementRule, Strategy
from migration_agent.spec.loader import load_config, load_spec

SPEC = {
    "spec_id": "example",
    "title": "Example migration",
    "target_version": "2.0",
    "deprecated_patterns": [
        {
            "pattern_id": "base",
            "ast_matcher": "sqlalchemy.ext.declarative.declarative_base",
        }
    ],
    "replacement_rules": [
        {"pattern_id": "base", "strategy": "codemod", "ref": "sqla_declarative_base"}
    ],
}


def write_spec(path: Path, value: object = SPEC) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def test_load_spec_validates_references_and_enums(tmp_path: Path) -> None:
    spec_path = write_spec(tmp_path / "spec.json")
    loaded = load_spec(spec_path)
    assert loaded.spec_id == "example"
    assert loaded.replacement_rules[0].strategy is Strategy.CODEMOD

    invalid = dict(SPEC)
    invalid["replacement_rules"] = [{"pattern_id": "missing", "strategy": "codemod", "ref": "x"}]
    with pytest.raises(ValueError, match="unknown pattern"):
        load_spec(write_spec(tmp_path / "invalid.json", invalid))


def test_load_config_rejects_unknown_and_invalid_overrides(tmp_path: Path) -> None:
    spec_path = write_spec(tmp_path / "spec.json")
    config = load_config(str(tmp_path), str(spec_path), dry_run=True, max_steps=10)
    assert config.dry_run and config.max_steps == 10
    with pytest.raises(ValueError, match="unknown"):
        load_config(str(tmp_path), str(spec_path), unsupported=True)
    with pytest.raises(ValueError, match="non-negative"):
        load_config(str(tmp_path), str(spec_path), batch_size=-1)


def test_declarative_base_codemod_rewrites_imports_and_aliases(tmp_path: Path) -> None:
    source_path = tmp_path / "models.py"
    source_path.write_text(
        "from sqlalchemy.ext.declarative import declarative_base as make_base\n"
        "import sqlalchemy.ext.declarative\n"
        "Base = make_base()\n",
        encoding="utf-8",
    )
    rule = ReplacementRule("base", Strategy.CODEMOD, "sqla_declarative_base")
    patch = apply_codemod(rule, str(source_path))
    assert patch is not None
    assert "from sqlalchemy.orm import declarative_base as make_base" in source_path.read_text(
        encoding="utf-8"
    )
    assert "import sqlalchemy.orm" in source_path.read_text(encoding="utf-8")
    assert validate(patch, str(tmp_path))


def test_codemod_returns_none_when_no_change(tmp_path: Path) -> None:
    source_path = tmp_path / "models.py"
    source_path.write_text("from sqlalchemy.orm import declarative_base\n", encoding="utf-8")
    rule = ReplacementRule("base", Strategy.CODEMOD, "sqla_declarative_base")
    assert apply_codemod(rule, str(source_path)) is None


def test_validate_rejects_invalid_python(tmp_path: Path) -> None:
    source_path = tmp_path / "broken.py"
    source_path.write_text("def broken(:\n", encoding="utf-8")
    patch = Patch(str(source_path), "", Strategy.CODEMOD)
    assert not validate(patch, str(tmp_path))


def test_status_command_lists_python_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "app.py").write_text("value = 1\n", encoding="utf-8")
    assert main(["status", "--repo", str(tmp_path)]) == 0
    assert '"path": "app.py"' in capsys.readouterr().out
