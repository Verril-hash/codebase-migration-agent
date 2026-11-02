"""Turn a failing test report into targeted fix requests.

Owner: Prajwal (Module 7). Design: SDD section 5.3: parse the trace, map
frames to graph nodes, walk up to the nearest edited node.

Each failure's traceback is parsed into frames and the frames are mapped to files
of the graph memory. The file to fix is, in this order:

1. the innermost frame in a file this batch edited: the edit raised the error, or
   called the code that did;
2. an edited function that a frame on the stack calls (call graph): the edit
   already returned, leaving a broken value behind;
3. the nearest edited file the failing test depends on (import and call edges);
4. the only edited file, when there is exactly one.

Test files are never a target, because tests are the judge of a migration and are
never edited (CLAUDE.md, rule 1). A failure no rule can place gets no request, and
the agent rolls the batch back.
"""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass
from pathlib import PurePosixPath

from migration_agent.analysis.parser import module_name
from migration_agent.memory.graph_memory import GraphMemory
from migration_agent.models import AnalysisResult, FileState, FixRequest, TestFailure, TestReport

_NATIVE_FRAME = re.compile(r'File "(?P<file>[^"]+)", line (?P<line>\d+)(?:, in (?P<function>\S+))?')
_PYTEST_FRAME = re.compile(
    r"^(?P<file>(?:[A-Za-z]:)?[^\s:][^:\n]*?\.py):(?P<line>\d+):(?: in (?P<function>\S+))?"
)
_LINT_FINDING = re.compile(r"^(?P<path>.+?):(?P<line>\d+):(?P<column>\d+): (?P<message>.+)$")
_LIBRARY_MARKERS = ("site-packages/", "dist-packages/", "/lib/python")
_SUMMARY_LIMIT = 200


@dataclass(frozen=True)
class Frame:
    """One traceback frame, as written in the traceback."""

    file: str
    line: int
    function: str


def parse_frames(text: str) -> list[Frame]:
    """Frames of a traceback, outermost first.

    Reads Python's own format (``File "app/db.py", line 12, in get_user``) and
    pytest's (``app/db.py:12: in get_user``); chained tracebacks are read in order.
    """
    frames = []
    for line in text.splitlines():
        match = _NATIVE_FRAME.search(line) or _PYTEST_FRAME.match(line.strip())
        if match:
            frames.append(Frame(match["file"], int(match["line"]), match["function"] or ""))
    return frames


def is_test_file(path: str) -> bool:
    """True for a test module, a conftest or anything under a tests/ folder."""
    posix = PurePosixPath(path.replace("\\", "/"))
    name = posix.name
    return (
        name == "conftest.py"
        or name.startswith("test_")
        or name.endswith("_test.py")
        or any(part in ("tests", "test") for part in posix.parts[:-1])
    )


class _Index:
    """Lookups between files, module names and qualified function names."""

    def __init__(self, analysis: AnalysisResult, memory: GraphMemory) -> None:
        self.memory = memory
        self.call_graph = analysis.call_graph
        self.functions = set(analysis.call_graph)
        for callees in analysis.call_graph.values():
            self.functions.update(callees)
        self.module_of: dict[str, str] = {}
        for path in memory.paths():
            if path.endswith(".py"):
                self.module_of[path] = self._module(path)
        self.file_of = {module: path for path, module in self.module_of.items()}

    def _module(self, path: str) -> str:
        if self.memory.repo_root is not None:
            return module_name(path, self.memory.repo_root)
        parts = path[:-3].split("/")
        if parts[-1] == "__init__" and len(parts) > 1:
            parts = parts[:-1]
        return ".".join(parts)

    def repo_path(self, raw: str) -> str | None:
        """Repository-relative path of a traceback file, or None for library code."""
        path = raw.replace("\\", "/")
        while path.startswith("./"):
            path = path[2:]
        if path in self.memory:
            return path
        if any(marker in path.lower() for marker in _LIBRARY_MARKERS):
            return None
        root = self.memory.repo_root
        if root is not None:
            prefix = root.resolve().as_posix().rstrip("/") + "/"
            if path.lower().startswith(prefix.lower()) and path[len(prefix) :] in self.memory:
                return path[len(prefix) :]
        endings = [p for p in self.memory.paths() if path.endswith("/" + p)]
        return max(endings, key=len) if endings else None

    def qualified(self, path: str, function: str) -> list[str]:
        """Qualified names a frame's function may have, e.g. ``app.db.Repo.get``."""
        module = self.module_of.get(path)
        if module is None:
            return []
        if function in ("", "<module>"):
            name = f"{module}.<module>"
            return [name] if name in self.functions else []
        return sorted(
            name
            for name in self.functions
            if name.startswith(module + ".") and name.endswith("." + function)
        )

    def owner(self, qualified: str) -> str | None:
        """File that defines a qualified function, by its longest module prefix."""
        parts = qualified.split(".")
        for end in range(len(parts) - 1, 0, -1):
            found = self.file_of.get(".".join(parts[:end]))
            if found is not None:
                return found
        return None


def _summary(failure: TestFailure) -> str:
    """The error in one line: the message's first line, else the traceback's last."""
    message = [line.strip() for line in failure.message.splitlines() if line.strip()]
    if message:
        return message[0][:_SUMMARY_LIMIT]
    traceback = [line.strip() for line in failure.traceback.splitlines() if line.strip()]
    return traceback[-1][:_SUMMARY_LIMIT] if traceback else failure.test_id


def _name(index: _Index, path: str, function: str) -> str:
    names = index.qualified(path, function)
    if names:
        return names[0]
    return "" if function == "<module>" else function


def _locate(failure: TestFailure, index: _Index, edited: set[str]) -> tuple[str, str, str] | None:
    """(file, function, reason) of the edit most likely to have caused ``failure``."""
    summary = _summary(failure)
    frames = parse_frames(f"{failure.traceback}\n{failure.message}")
    located = [(path, f) for f in frames if (path := index.repo_path(f.file)) is not None]
    sources = [(path, f) for path, f in located if not is_test_file(path)]

    for depth, (path, frame) in enumerate(reversed(sources)):
        if path in edited:
            where = f"{path}:{frame.line} in {frame.function or 'module code'}"
            if depth == 0:
                reason = f"{summary}; raised at {where}, which this batch edited"
            else:
                reason = f"{summary}; {where} was edited and calls the code that failed"
            return path, _name(index, path, frame.function), reason

    for path, frame in reversed(located):
        for caller in index.qualified(path, frame.function):
            for callee in index.call_graph.get(caller, []):
                owner = index.owner(callee)
                if owner in edited and not is_test_file(owner):
                    reason = f"{summary}; {caller} calls {callee}, which this batch edited"
                    return owner, callee, reason

    start = index.repo_path(failure.test_id.split("::", 1)[0])
    if start is None and located:
        start = located[0][0]
    if start is not None:
        seen, queue = {start}, deque([start])
        while queue:
            current = queue.popleft()
            if current != start and current in edited and not is_test_file(current):
                reason = (
                    f"{summary}; the failing test depends on {current}, which this batch edited"
                )
                return current, "", reason
            for neighbour in index.memory.get(current).dependencies:
                if neighbour not in seen and neighbour in index.memory:
                    seen.add(neighbour)
                    queue.append(neighbour)

    candidates = sorted(path for path in edited if not is_test_file(path))
    if len(candidates) == 1:
        return candidates[0], "", f"{summary}; {candidates[0]} is the only file this batch edited"
    return None


def diagnose(report: TestReport, analysis: AnalysisResult, memory: GraphMemory) -> list[FixRequest]:
    """One fix request per edited location that a failure or lint finding points to.

    Failures that point to the same file and function share one request (the first
    failure's), so one broken edit is repaired once. The order is the report's.
    """
    index = _Index(analysis, memory)
    edited = set(memory.in_state(FileState.EDITED))
    requests: list[FixRequest] = []
    seen: set[tuple[str, str]] = set()

    def add(file: str, function: str, failure: TestFailure, reason: str) -> None:
        if (file, function) not in seen:
            seen.add((file, function))
            requests.append(FixRequest(file, function, failure, reason))

    for failure in report.failures:
        found = _locate(failure, index, edited)
        if found is not None:
            add(found[0], found[1], failure, found[2])

    for finding in report.lint_findings:
        match = _LINT_FINDING.match(finding.strip())
        if match is None:
            continue
        path = index.repo_path(match["path"])
        if path is None or path not in edited or is_test_file(path):
            continue
        line, message = match["line"], match["message"]
        failure = TestFailure(f"lint::{path}:{line}", message, finding)
        add(path, "", failure, f"the linter reports '{message}' at {path}:{line}, which was edited")
    return requests


def _header_path(text: str) -> str:
    path = text.split("\t", 1)[0].strip().strip('"')
    if path.startswith(("a/", "b/")):
        path = path[2:]
    return path


def _changes(diff: str) -> tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...]:
    """What a unified diff changes: per file, its removed and added lines, in order.

    Hunk positions, index lines and trailing whitespace do not count.
    """
    lines = diff.splitlines()
    removed: dict[str, list[str]] = {}
    added: dict[str, list[str]] = {}
    current = ""
    skip_next = False
    for i, line in enumerate(lines):
        if skip_next:
            skip_next = False
            continue
        if line.startswith("--- ") and i + 1 < len(lines) and lines[i + 1].startswith("+++ "):
            old, new = _header_path(line[4:]), _header_path(lines[i + 1][4:])
            current = new if new != "/dev/null" else old
            skip_next = True
        elif line.startswith("+"):
            added.setdefault(current, []).append(line[1:].rstrip())
        elif line.startswith("-"):
            removed.setdefault(current, []).append(line[1:].rstrip())
    files = sorted(set(removed) | set(added))
    return tuple((f, tuple(removed.get(f, [])), tuple(added.get(f, []))) for f in files)


def is_oscillating(patch_history: list[str], new_patch: str) -> bool:
    """True when a new patch repeats an earlier, already rejected one.

    Patches are compared by the lines they change, so a repeat at another position
    is caught. A patch that undoes an earlier one counts as a repeat (the repair
    would flip back and forth), and so does a patch that changes nothing, because
    applying it cannot make progress.
    """
    new = _changes(new_patch)
    if not new:
        return True
    for old_patch in patch_history:
        old = _changes(old_patch)
        undone = tuple((f, added, removed) for f, removed, added in old)
        if new in (old, undone):
            return True
    return False
