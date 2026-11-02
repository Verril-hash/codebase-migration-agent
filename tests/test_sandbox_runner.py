"""Tests for Module 6: the Docker sandbox, report parsing and the image builder.

The first part runs anywhere: the container is replaced by a fake. The tests
marked ``needs_docker`` run the real sandbox and are skipped when Docker is not
running or the image is not built (``python -m migration_agent.sandbox build``).
"""

import io
import subprocess
import tarfile
from pathlib import Path

import pytest

from migration_agent.sandbox import container, runner
from migration_agent.sandbox.__main__ import build_parser, main
from migration_agent.sandbox.container import (
    ContainerResult,
    Limits,
    SandboxError,
    create_args,
    docker_available,
    image_exists,
    write_archive,
)
from migration_agent.sandbox.image import (
    BASE_IMAGE,
    DEFAULT_PROFILE,
    PROFILES,
    build_image,
    default_image,
    dockerfile,
    image_tag,
)
from migration_agent.sandbox.runner import (
    JUNIT_PATH,
    parse_junit,
    parse_ruff,
    report_from_run,
    run_lint,
    run_tests,
)

MIXED = b"""<?xml version="1.0" encoding="utf-8"?>
<testsuites name="pytest tests"><testsuite name="pytest" errors="2" failures="1"
 skipped="2" tests="6" time="0.5">
<testcase classname="tests.test_bank" name="test_ok" file="tests/test_bank.py" line="4"
 time="0.01" />
<testcase classname="tests.test_bank.TestAccounts" name="test_total[2]"
 file="tests/test_bank.py" line="12" time="0.02">
<failure message="AttributeError: 'Engine' object has no attribute 'execute'">
Traceback (most recent call last):
  File "/work/tests/test_bank.py", line 14, in test_total
    assert stats.total(engine) == 2
  File "/work/app/stats.py", line 5, in total
    return engine.execute("select count(*) from users").scalar()
AttributeError: 'Engine' object has no attribute 'execute'</failure></testcase>
<testcase classname="tests.test_bank" name="test_setup_breaks" file="tests/test_bank.py"
 line="20" time="0.0">
<error message="failed on setup with &quot;RuntimeError: no database&quot;">
Traceback (most recent call last):
  File "/work/tests/conftest.py", line 3, in db
    raise RuntimeError("no database")
RuntimeError: no database</error></testcase>
<testcase classname="tests.test_bank" name="test_later" file="tests/test_bank.py" line="24"
 time="0.0"><skipped type="pytest.skip" message="later">/work/tests/test_bank.py:25: later</skipped>
</testcase>
<testcase classname="tests.test_bank" name="test_known_bug" file="tests/test_bank.py"
 line="28" time="0.0"><skipped type="pytest.xfail" message="known bug" /></testcase>
<testcase classname="" name="tests.test_broken" file="tests/test_broken.py" time="0.0">
<error message="collection failure">tests/test_broken.py:1: in &lt;module&gt;
    from app.missing import thing
E   ModuleNotFoundError: No module named 'app.missing'</error></testcase>
</testsuite></testsuites>
"""

PASSING = b"""<?xml version="1.0" encoding="utf-8"?><testsuites><testsuite name="pytest">
<testcase classname="tests.test_db" name="test_names" file="tests/test_db.py" line="1" />
</testsuite></testsuites>"""

EMPTY = b'<?xml version="1.0" encoding="utf-8"?><testsuites><testsuite name="pytest" />'
EMPTY += b"</testsuites>"


# ----------------------------------------------------------------------------- parsing
def test_parse_junit_counts_every_outcome_and_keeps_each_failure() -> None:
    report = parse_junit(MIXED)
    assert (report.passed, report.failed, report.errors, report.skipped) == (1, 1, 2, 2)
    assert [f.test_id for f in report.failures] == [
        "tests/test_bank.py::TestAccounts::test_total[2]",
        "tests/test_bank.py::test_setup_breaks",
        "tests/test_broken.py",
    ]
    assert (
        report.failures[0].message == "AttributeError: 'Engine' object has no attribute 'execute'"
    )
    assert not report.green


def test_tracebacks_use_repository_relative_paths() -> None:
    traceback = parse_junit(MIXED).failures[0].traceback
    assert 'File "app/stats.py", line 5, in total' in traceback
    assert "/work/" not in traceback


@pytest.mark.parametrize(
    "data", [b"not xml at all", b"<!DOCTYPE t [<!ENTITY a 'b'>]><testsuites>&a;</testsuites>"]
)
def test_an_unreadable_report_is_rejected(data: bytes) -> None:
    with pytest.raises(ValueError):
        parse_junit(data)


def test_a_clean_run_is_green() -> None:
    report = report_from_run(0, PASSING, "1 passed")
    assert report.green and report.passed == 1 and report.failures == []


def test_no_tests_collected_is_green_and_empty() -> None:
    report = report_from_run(5, EMPTY, "no tests ran")
    assert report.green and report.passed == 0


@pytest.mark.parametrize("exit_code", [1, 2, 3, 4, 137])
def test_a_report_that_contradicts_the_exit_code_is_not_believed(exit_code: int) -> None:
    report = report_from_run(exit_code, PASSING, "boom")
    assert not report.green
    assert report.failures[-1].test_id == "sandbox::pytest"
    assert str(exit_code) in report.failures[-1].message


@pytest.mark.parametrize("junit", [None, b"<broken"])
def test_a_missing_or_broken_report_is_an_error(junit: bytes | None) -> None:
    report = report_from_run(1, junit, 'Traceback ...\n  File "/work/conftest.py", line 1')
    assert report.errors == 1 and not report.green
    assert report.failures[0].test_id == "sandbox::pytest"
    assert 'File "conftest.py"' in report.failures[0].traceback


def test_parse_ruff_keeps_findings_and_drops_the_summary() -> None:
    output = (
        "app/bad.py:3:12: F821 Undefined name `sesion`\n"
        "/work/app/broken.py:1:9: SyntaxError: Expected ')', found newline\n"
        "Found 2 errors.\n"
    )
    assert parse_ruff(output) == [
        "app/bad.py:3:12: F821 Undefined name `sesion`",
        "app/broken.py:1:9: SyntaxError: Expected ')', found newline",
    ]
    assert parse_ruff("All checks passed!\n") == []


# ----------------------------------------------------------------------------- running, faked
class FakeContainer:
    def __init__(self, result: ContainerResult) -> None:
        self.result = result
        self.calls: list[dict] = []

    def __call__(self, repo_path, command, image, limits, collect=()) -> ContainerResult:
        self.calls.append(
            {"repo": repo_path, "command": list(command), "image": image, "collect": collect}
        )
        return self.result


def fake(monkeypatch: pytest.MonkeyPatch, result: ContainerResult) -> FakeContainer:
    container_fake = FakeContainer(result)
    monkeypatch.setattr(runner, "run_in_container", container_fake)
    return container_fake


def test_run_tests_runs_pytest_in_the_sandbox_and_parses_its_report(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("SANDBOX_IMAGE", raising=False)
    sandbox = fake(monkeypatch, ContainerResult(0, "1 passed", {JUNIT_PATH: PASSING}))
    report = run_tests(str(tmp_path), ["tests/test_db.py::test_names", "tests\\test_other.py"])
    assert report.green and report.passed == 1
    call = sandbox.calls[0]
    assert call["image"] == image_tag(DEFAULT_PROFILE)
    assert call["collect"] == [JUNIT_PATH]
    assert call["command"][:3] == ["python", "-m", "pytest"]
    for option in ("no:cacheprovider", "--tb=native", "junit_family=xunit1"):
        assert option in call["command"]
    assert f"--junitxml={JUNIT_PATH}" in call["command"]
    assert call["command"][-2:] == ["tests/test_db.py::test_names", "tests/test_other.py"]


def test_the_image_can_be_chosen_by_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("SANDBOX_IMAGE", "my-sandbox:custom")
    sandbox = fake(monkeypatch, ContainerResult(0, "", {JUNIT_PATH: PASSING}))
    run_tests(str(tmp_path))
    assert sandbox.calls[0]["image"] == "my-sandbox:custom"


def test_a_timeout_is_an_error_and_never_green(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake(monkeypatch, ContainerResult(None, "test_slow ..."))
    report = run_tests(str(tmp_path), limits=Limits(timeout=5))
    assert not report.green and report.errors == 1
    assert report.failures[0].test_id == "sandbox::timeout"
    assert "5 s" in report.failures[0].message


@pytest.mark.parametrize(
    "item", ["-k", "--rootdir=/", "/etc/passwd", "../outside.py", "tests/../../x.py", "C:\\x.py"]
)
def test_a_selection_must_be_repository_relative(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, item: str
) -> None:
    sandbox = fake(monkeypatch, ContainerResult(0, "", {JUNIT_PATH: PASSING}))
    with pytest.raises(ValueError, match="repository-relative"):
        run_tests(str(tmp_path), [item])
    assert sandbox.calls == []


def test_run_lint_checks_only_breaking_rules_and_returns_findings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    sandbox = fake(monkeypatch, ContainerResult(1, "app/bad.py:3:12: F821 Undefined name `x`\n"))
    assert run_lint(str(tmp_path), ["app/bad.py"]) == ["app/bad.py:3:12: F821 Undefined name `x`"]
    command = sandbox.calls[0]["command"]
    assert command[:2] == ["ruff", "check"] and "--isolated" in command
    assert f"--select={runner.LINT_RULES}" in command and command[-1] == "app/bad.py"
    run_lint(str(tmp_path), [])
    assert sandbox.calls[1]["command"][-1] == "."


@pytest.mark.parametrize("result", [ContainerResult(2, "error: bad flag"), ContainerResult(None)])
def test_a_linter_that_cannot_run_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, result: ContainerResult
) -> None:
    fake(monkeypatch, result)
    with pytest.raises(SandboxError):
        run_lint(str(tmp_path), ["app/x.py"])


# ----------------------------------------------------------------------------- container
def test_limits_come_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SANDBOX_TIMEOUT", raising=False)
    assert Limits.from_env().timeout == 600
    monkeypatch.setenv("SANDBOX_TIMEOUT", "30")
    assert Limits.from_env().timeout == 30
    for bad in ("soon", "0", "-5"):
        monkeypatch.setenv("SANDBOX_TIMEOUT", bad)
        with pytest.raises(ValueError, match="SANDBOX_TIMEOUT"):
            Limits.from_env()


def test_the_container_has_no_network_capabilities_or_pulls() -> None:
    args = create_args("box", "img:tag", ["python", "-V"], Limits(memory="1g", pids=64))

    def value(flag: str) -> str:
        return args[args.index(flag) + 1]

    assert args[0] == "create"
    assert value("--network") == "none"
    assert value("--cap-drop") == "ALL"
    assert value("--security-opt") == "no-new-privileges"
    assert value("--pull") == "never"
    assert value("--memory") == value("--memory-swap") == "1g"
    assert value("--pids-limit") == "64"
    assert args[-3:] == ["img:tag", "python", "-V"]


def test_the_archive_leaves_out_version_control_caches_and_virtualenvs(tmp_path: Path) -> None:
    files = {
        "app/db.py": "X = 1\n",
        "tests/test_db.py": "def test_x():\n    pass\n",
        ".git/config": "[core]\n",
        "app/__pycache__/db.cpython-312.pyc": "",
        "app/old.pyc": "",
        "myenv/pyvenv.cfg": "home = /usr\n",
        "myenv/lib/site.py": "",
        ".migration_agent/graph_memory.json": "{}",
        "node_modules/a.js": "",
    }
    for name, text in files.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_bytes(text.encode("utf-8"))  # exact bytes on every OS
    buffer = io.BytesIO()
    names = write_archive(tmp_path, buffer)
    assert sorted(names) == ["app", "app/db.py", "tests", "tests/test_db.py"]
    buffer.seek(0)
    with tarfile.open(fileobj=buffer) as tar:
        member = tar.getmember("app/db.py")
        assert (member.uid, member.gid) == (0, 0)
        assert tar.extractfile(member).read() == b"X = 1\n"


def test_a_missing_docker_or_image_is_a_clear_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    engine = {"running": False}

    def fake_docker(*args, **kwargs):
        if args[0] == "version":
            return subprocess.CompletedProcess(args, 0 if engine["running"] else 1, b"", b"")
        if args[0] == "image":
            return subprocess.CompletedProcess(args, 1, b"", b"No such image")
        raise AssertionError(f"no container may be created: {args}")

    monkeypatch.setattr(container, "_docker", fake_docker)
    with pytest.raises(SandboxError, match="Docker is not running"):
        container.run_in_container(tmp_path, ["true"], "img:tag", Limits())
    engine["running"] = True
    with pytest.raises(SandboxError, match="is not built"):
        container.run_in_container(tmp_path, ["true"], "img:tag", Limits())


def test_a_missing_repository_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(SandboxError, match="does not exist"):
        container.run_in_container(tmp_path / "missing", ["true"], "img:tag", Limits())


# ----------------------------------------------------------------------------- images
def test_image_tags_and_dockerfile_pin_everything(monkeypatch: pytest.MonkeyPatch) -> None:
    assert image_tag("sqla-1.4") == "migration-agent-sandbox:sqla-1.4"
    with pytest.raises(ValueError, match="unknown sandbox profile"):
        image_tag("sqla-3.0")
    monkeypatch.delenv("SANDBOX_IMAGE", raising=False)
    assert default_image() == image_tag(DEFAULT_PROFILE)
    text = dockerfile(["pytest==9.2.0", "SQLAlchemy==2.0.55"])
    assert text.startswith(f"FROM {BASE_IMAGE}\n")
    assert "pip install pytest==9.2.0 SQLAlchemy==2.0.55" in text
    for packages in PROFILES.values():
        assert all("==" in package for package in packages)


@pytest.mark.parametrize("extra", ["flask", "flask>=3", "flask==3.0; rm -rf /"])
def test_extra_packages_must_be_pinned(extra: str) -> None:
    with pytest.raises(ValueError, match="pinned"):
        build_image(DEFAULT_PROFILE, [extra])


def test_the_command_line_parses_and_reports_bad_input(capsys: pytest.CaptureFixture) -> None:
    args = build_parser().parse_args(["build", "--profile", "all", "--with", "flask==3.0.3"])
    assert (args.command, args.profile, args.extra) == ("build", "all", ["flask==3.0.3"])
    assert main(["build", "--with", "flask"]) == 1
    assert "pinned" in capsys.readouterr().err


# ----------------------------------------------------------------------------- real Docker
DOCKER_READY = docker_available() and image_exists(default_image())
needs_docker = pytest.mark.skipif(
    not DOCKER_READY,
    reason="Docker is not running or the sandbox image is not built "
    "(python -m migration_agent.sandbox build)",
)

CALC = "def add(a, b):\n    return a + b\n"
CALC_TESTS = """import pytest

from app.calc import add


def test_passes():
    assert add(1, 2) == 3


def test_fails():
    assert add(1, 2) == 4


@pytest.fixture
def broken():
    raise RuntimeError("no database")


def test_errors(broken):
    pass


@pytest.mark.skip(reason="later")
def test_skipped():
    pass
"""


def make_repo(root: Path, files: dict[str, str]) -> Path:
    for name, text in {"conftest.py": "", "app/__init__.py": "", **files}.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text, encoding="utf-8")
    return root


def snapshot(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


@needs_docker
def test_a_real_run_reports_each_outcome(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, {"app/calc.py": CALC, "tests/test_calc.py": CALC_TESTS})
    report = run_tests(str(repo))
    assert (report.passed, report.failed, report.errors, report.skipped) == (1, 1, 1, 1)
    by_id = {failure.test_id: failure for failure in report.failures}
    assert set(by_id) == {"tests/test_calc.py::test_fails", "tests/test_calc.py::test_errors"}
    traceback = by_id["tests/test_calc.py::test_fails"].traceback
    assert 'File "tests/test_calc.py"' in traceback and "/work/" not in traceback


@needs_docker
def test_a_selection_runs_only_the_named_tests(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, {"app/calc.py": CALC, "tests/test_calc.py": CALC_TESTS})
    report = run_tests(str(repo), ["tests/test_calc.py::test_passes"])
    assert report.green and report.passed == 1 and report.failed == 0


@needs_docker
def test_the_sandbox_has_no_network(tmp_path: Path) -> None:
    test = (
        "import socket\n\nimport pytest\n\n\ndef test_offline():\n"
        "    with pytest.raises(OSError):\n"
        "        socket.create_connection(('1.1.1.1', 53), timeout=3)\n"
    )
    report = run_tests(str(make_repo(tmp_path, {"tests/test_net.py": test})))
    assert report.green and report.passed == 1


@needs_docker
def test_the_working_copy_is_never_changed(tmp_path: Path) -> None:
    test = (
        "from pathlib import Path\n\nfrom app.calc import add\n\n\ndef test_writes():\n"
        "    Path('written.txt').write_text('x')\n    assert add(1, 1) == 2\n"
    )
    repo = make_repo(tmp_path, {"app/calc.py": CALC, "tests/test_write.py": test})
    before = snapshot(repo)
    assert run_tests(str(repo)).green
    assert snapshot(repo) == before


@needs_docker
def test_a_hanging_suite_is_stopped_and_its_container_removed(tmp_path: Path) -> None:
    test = "import time\n\n\ndef test_hangs():\n    time.sleep(300)\n"
    repo = make_repo(tmp_path, {"tests/test_hang.py": test})
    report = run_tests(str(repo), limits=Limits(timeout=15))
    assert not report.green and report.failures[0].test_id == "sandbox::timeout"
    left = subprocess.run(
        ["docker", "ps", "-aq", "--filter", f"label={container.LABEL}"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert left.stdout.strip() == ""


@needs_docker
def test_a_module_that_fails_to_import_is_an_error(tmp_path: Path) -> None:
    test = "from app.missing import thing\n\n\ndef test_x():\n    assert thing\n"
    report = run_tests(str(make_repo(tmp_path, {"tests/test_broken.py": test})))
    assert not report.green and report.errors >= 1
    failure = report.failures[0]
    assert "test_broken" in failure.test_id
    assert "app.missing" in failure.message + failure.traceback


@needs_docker
def test_the_default_image_has_the_target_library(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("SANDBOX_IMAGE", raising=False)
    version = PROFILES[DEFAULT_PROFILE][0].split("==")[1]
    test = (
        "import sqlalchemy\n\n\ndef test_version():\n"
        f"    assert sqlalchemy.__version__ == '{version}'\n"
    )
    assert run_tests(str(make_repo(tmp_path, {"tests/test_version.py": test}))).green


@needs_docker
def test_run_lint_finds_undefined_names_and_syntax_errors(tmp_path: Path) -> None:
    repo = make_repo(
        tmp_path,
        {
            "app/bad.py": "def f():\n    return sesion\n",
            "app/broken.py": "def f(:\n    pass\n",
            "app/good.py": "X = 1\n",
        },
    )
    findings = run_lint(str(repo), ["app/bad.py", "app/broken.py", "app/good.py"])
    assert any(f.startswith("app/bad.py:2:12: F821") for f in findings)
    assert any(f.startswith("app/broken.py:1:") for f in findings)
    assert not any(f.startswith("app/good.py") for f in findings)
    assert run_lint(str(repo), ["app/good.py"]) == []
