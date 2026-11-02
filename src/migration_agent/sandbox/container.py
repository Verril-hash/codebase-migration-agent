"""Run one command in a throwaway Docker container.

Owner: Prajwal (Module 6). Design: SDD section 9 and figure 6: the container gets
an isolated copy of the repository, no network and resource limits, and the
working copy on the host is never mounted, so nothing the tests do can reach it.

The copy goes in as a tar stream (``docker cp -``) into a created container and
result files come back the same way. This works the same with Docker Desktop on
Windows and with Docker on Linux, with no host folder shared with the container.
"""

from __future__ import annotations

import io
import os
import subprocess
import tarfile
import tempfile
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO

#: Where the repository copy lives inside the container.
WORKDIR = "/work"
#: Label on every sandbox container, so a leftover one is easy to find.
LABEL = "migration-agent.sandbox"
#: Folders never copied into the sandbox: version control, caches and agent state.
SKIP_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".tox",
        ".nox",
        "node_modules",
        ".migration_agent",
    }
)
SKIP_SUFFIXES = (".pyc", ".pyo")
#: Characters of command output kept, from the end, where the useful part is.
OUTPUT_LIMIT = 50_000
#: Largest result file read back from a container.
RESULT_LIMIT = 50_000_000


class SandboxError(RuntimeError):
    """Docker is missing, the image is not built, or the sandbox itself failed."""


@dataclass(frozen=True)
class Limits:
    """Resources one sandbox run may use."""

    #: Seconds the command may run before the container is killed.
    timeout: float = 600.0
    memory: str = "2g"
    cpus: str = "1"
    pids: int = 512

    @classmethod
    def from_env(cls) -> Limits:
        """Defaults, with the timeout taken from ``SANDBOX_TIMEOUT`` when it is set."""
        raw = os.environ.get("SANDBOX_TIMEOUT", "").strip()
        if not raw:
            return cls()
        try:
            timeout = float(raw)
        except ValueError:
            raise ValueError(f"SANDBOX_TIMEOUT must be a number of seconds, got {raw!r}") from None
        if timeout <= 0:
            raise ValueError(f"SANDBOX_TIMEOUT must be positive, got {raw!r}")
        return cls(timeout=timeout)


@dataclass
class ContainerResult:
    """What one command left behind: exit code, output and the files asked for."""

    #: None when the command was stopped at the timeout.
    exit_code: int | None
    #: Combined standard output and error, cut to its last OUTPUT_LIMIT characters.
    output: str = ""
    #: Container path -> content, for every requested file that exists.
    files: dict[str, bytes] = field(default_factory=dict)

    @property
    def timed_out(self) -> bool:
        return self.exit_code is None


def _docker(
    *args: str, timeout: float = 120, stdin: BinaryIO | None = None, check: bool = True
) -> subprocess.CompletedProcess[bytes]:
    try:
        result = subprocess.run(
            ["docker", *args],
            stdin=stdin if stdin is not None else subprocess.DEVNULL,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        raise SandboxError("Docker is not installed or not on PATH") from None
    except subprocess.TimeoutExpired:
        raise SandboxError(f"docker {args[0]} took longer than {timeout:g} s") from None
    if check and result.returncode != 0:
        raise SandboxError(f"docker {args[0]} failed: {_text(result.stderr or result.stdout)}")
    return result


def _text(data: bytes) -> str:
    return data.decode("utf-8", errors="replace").strip()


def docker_available() -> bool:
    """True when the Docker command exists and its engine answers."""
    try:
        return _docker("version", "--format", "{{.Server.Version}}", timeout=30).returncode == 0
    except SandboxError:
        return False


def image_exists(image: str) -> bool:
    """True when ``image`` is built locally (it is never pulled)."""
    try:
        return _docker("image", "inspect", image, timeout=30, check=False).returncode == 0
    except SandboxError:
        return False


def _require_image(image: str) -> None:
    result = _docker("image", "inspect", image, timeout=30, check=False)
    if result.returncode == 0:
        return
    if not docker_available():
        raise SandboxError(
            "Docker is not running; start Docker Desktop (or the Docker service) and retry"
        )
    raise SandboxError(
        f"sandbox image {image!r} is not built; run: python -m migration_agent.sandbox build"
    )


def _skipped(path: Path) -> bool:
    if path.is_dir() and not path.is_symlink():
        return path.name in SKIP_DIRS or (path / "pyvenv.cfg").is_file()
    return path.suffix in SKIP_SUFFIXES


def write_archive(root: Path, out: BinaryIO) -> list[str]:
    """Write the repository as a tar archive for ``docker cp``; return its member names.

    Version control, caches, virtual environments and compiled files are left out.
    Every member is owned by root, the user the sandbox runs as.
    """

    def owned_by_root(info: tarfile.TarInfo) -> tarfile.TarInfo:
        info.uid = info.gid = 0
        info.uname = info.gname = "root"
        return info

    names: list[str] = []
    with tarfile.open(fileobj=out, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for current, dirs, files in os.walk(root):
            here = Path(current)
            entries = sorted(d for d in dirs if not _skipped(here / d))
            entries += sorted(f for f in files if not _skipped(here / f))
            dirs[:] = [d for d in entries if (here / d).is_dir() and not (here / d).is_symlink()]
            for name in entries:
                path = here / name
                arcname = path.relative_to(root).as_posix()
                tar.add(path, arcname=arcname, recursive=False, filter=owned_by_root)
                names.append(arcname)
    return names


def create_args(name: str, image: str, command: Sequence[str], limits: Limits) -> list[str]:
    """Arguments of ``docker create`` for one locked-down sandbox container."""
    return [
        "create",
        "--name",
        name,
        "--label",
        f"{LABEL}=1",
        "--pull",
        "never",
        "--network",
        "none",
        "--memory",
        limits.memory,
        "--memory-swap",
        limits.memory,
        "--cpus",
        limits.cpus,
        "--pids-limit",
        str(limits.pids),
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--env",
        "PYTHONDONTWRITEBYTECODE=1",
        "--env",
        "PYTHONHASHSEED=0",
        "--workdir",
        WORKDIR,
        image,
        *command,
    ]


def _read_back(name: str, path: str) -> bytes | None:
    result = _docker("cp", f"{name}:{path}", "-", check=False)
    if result.returncode != 0:
        return None
    with tarfile.open(fileobj=io.BytesIO(result.stdout)) as tar:
        for member in tar:
            if member.isfile() and member.size <= RESULT_LIMIT:
                extracted = tar.extractfile(member)
                return extracted.read() if extracted is not None else None
    return None


def run_in_container(
    repo_path: str | Path,
    command: Sequence[str],
    image: str,
    limits: Limits,
    collect: Sequence[str] = (),
) -> ContainerResult:
    """Run ``command`` on a copy of the repository in a new container, then remove it.

    ``collect`` names files to read back from the container after the command ends.
    Raise :class:`SandboxError` when Docker or the image is missing; a command that
    fails or times out is reported in the result instead.
    """
    root = Path(repo_path).resolve()
    if not root.is_dir():
        raise SandboxError(f"repository folder does not exist: {root}")
    _require_image(image)
    name = f"migration-sandbox-{uuid.uuid4().hex[:12]}"
    _docker(*create_args(name, image, command, limits))
    try:
        with tempfile.TemporaryFile() as archive:
            write_archive(root, archive)
            archive.seek(0)
            _docker("cp", "-", f"{name}:{WORKDIR}", stdin=archive, timeout=600)
        with tempfile.TemporaryFile() as log:
            try:
                started = subprocess.run(
                    ["docker", "start", "--attach", name],
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    timeout=limits.timeout,
                    check=False,
                )
                exit_code: int | None = started.returncode
            except subprocess.TimeoutExpired:
                exit_code = None
                _docker("kill", name, check=False)
            log.seek(0, os.SEEK_END)
            log.seek(max(0, log.tell() - OUTPUT_LIMIT * 4))
            output = log.read().decode("utf-8", errors="replace")[-OUTPUT_LIMIT:]
        files: dict[str, bytes] = {}
        if exit_code is not None:
            for path in collect:
                data = _read_back(name, path)
                if data is not None:
                    files[path] = data
        return ContainerResult(exit_code, output, files)
    finally:
        _docker("rm", "--force", name, check=False)
