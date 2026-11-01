"""Persistent state of every file, outside the language model prompt.

Owner: Prabanjan (Module 4). Design: SDD section 6.1 (FileNode record).

The graph memory is the blackboard of the system (SDD section 4.1): every other
module reads and writes file state here instead of passing its output along the
pipeline. One :class:`~migration_agent.models.FileNode` exists per source file,
keyed by repository-relative POSIX path.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections import Counter
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from migration_agent.analysis.parser import module_name
from migration_agent.models import (
    AnalysisResult,
    CallSite,
    FileNode,
    FileState,
    ParseFailure,
    UnresolvedReason,
    UnresolvedSite,
)

#: Version of the JSON layout written by :meth:`GraphMemory.save`.
SCHEMA_VERSION = 1
#: A node summary is cut to this many characters (about 60 tokens, SDD table 3).
SUMMARY_LIMIT = 240
#: Where a run keeps its memory file, relative to the repository being migrated.
STATE_FILE = Path(".migration_agent") / "graph_memory.json"

#: Call-site status that goes with each file state (CallSite.status, SDD section 6.1).
_SITE_STATUS = {
    FileState.PENDING: "pending",
    FileState.ANALYSED: "pending",
    FileState.EDITED: "patched",
    FileState.VERIFIED: "verified",
    FileState.FAILED: "pending",
    FileState.ROLLED_BACK: "pending",
}


def default_state_path(repo_path: str | Path) -> Path:
    """Default location of the memory file for a repository."""
    return Path(repo_path) / STATE_FILE


def hash_content(data: bytes) -> str:
    """Content hash used for drift detection (SHA-256 hex digest)."""
    return hashlib.sha256(data).hexdigest()


def _module_names(paths: Iterable[str], repo_root: Path | None = None) -> dict[str, str]:
    """Dotted module name -> file path, e.g. ``app.db`` -> ``app/db.py``.

    With ``repo_root`` the names follow Module 2 (package folders only, so
    ``src/app/db.py`` is ``app.db``); without it they come from the path alone.
    """
    names: dict[str, str] = {}
    for path in paths:
        if not path.endswith(".py"):
            continue
        if repo_root is not None:
            names[module_name(path, repo_root)] = path
            continue
        parts = path[:-3].split("/")
        if parts[-1] == "__init__":
            parts = parts[:-1]
        if parts:
            names[".".join(parts)] = path
    return names


def _owning_file(qualified: str, modules: dict[str, str]) -> str | None:
    """File that defines a qualified function name, by longest module prefix."""
    parts = qualified.split(".")
    for end in range(len(parts) - 1, 0, -1):
        found = modules.get(".".join(parts[:end]))
        if found is not None:
            return found
    return None


class GraphMemory:
    """Graph of :class:`FileNode` records: state, neighbours, call sites and notes."""

    def __init__(self, repo_root: str | Path | None = None) -> None:
        self._nodes: dict[str, FileNode] = {}
        #: Calls Module 2 could not resolve, kept per file (SDD section 5.1).
        self.unresolved: dict[str, list[UnresolvedSite]] = {}
        #: Files that could not be parsed; they are never edited.
        self.parse_failures: list[ParseFailure] = []
        #: Root of the repository, used to read sources for hashing and context.
        self.repo_root: Path | None = Path(repo_root) if repo_root is not None else None

    # ------------------------------------------------------------------ building
    @classmethod
    def from_analysis(
        cls, analysis: AnalysisResult, repo_root: str | Path | None = None
    ) -> GraphMemory:
        """One FileNode per file, with its dependencies and call sites filled in.

        Dependencies are the import edges of Module 2 plus the files reached through
        the call graph; dependents are the reverse edges. Every node starts in
        ``ANALYSED`` because static analysis has finished for it (SDD figure 5).
        """
        memory = cls(repo_root)
        files: set[str] = set(analysis.import_graph)
        for targets in analysis.import_graph.values():
            files.update(targets)
        files.update(site.file for site in analysis.call_sites)
        files.update(site.file for site in analysis.unresolved_sites)
        files.update(analysis.impact_set.files)
        files.update(failure.file for failure in analysis.parse_failures)

        edges: dict[str, set[str]] = {path: set() for path in files}
        for source, targets in analysis.import_graph.items():
            edges[source].update(targets)
        modules = _module_names(files, memory.repo_root)
        for caller, callees in analysis.call_graph.items():
            source = _owning_file(caller, modules)
            for callee in callees:
                target = _owning_file(callee, modules)
                if source and target and source in edges:
                    edges[source].add(target)

        reverse: dict[str, set[str]] = {path: set() for path in files}
        for source, targets in edges.items():
            targets.discard(source)
            for target in targets:
                reverse.setdefault(target, set()).add(source)

        sites: dict[str, list[CallSite]] = {}
        for site in analysis.call_sites:
            sites.setdefault(site.file, []).append(site)

        for path in sorted(files):
            memory._nodes[path] = FileNode(
                path=path,
                state=FileState.ANALYSED,
                dependencies=sorted(edges[path]),
                dependents=sorted(reverse.get(path, ())),
                call_sites=sorted(sites.get(path, []), key=lambda s: (s.line, s.column)),
            )
        for unresolved in analysis.unresolved_sites:
            memory.unresolved.setdefault(unresolved.file, []).append(unresolved)
        memory.parse_failures = list(analysis.parse_failures)
        for failure in memory.parse_failures:
            node = memory._nodes.get(failure.file)
            if node is not None:
                node.state = FileState.FAILED
        return memory

    # ------------------------------------------------------------------ reading
    def get(self, path: str) -> FileNode:
        """The node for ``path``; raises KeyError naming the path when it is unknown."""
        try:
            return self._nodes[path]
        except KeyError:
            raise KeyError(f"no file in graph memory: {path!r}") from None

    def __contains__(self, path: object) -> bool:
        return path in self._nodes

    def __len__(self) -> int:
        return len(self._nodes)

    def __iter__(self) -> Iterator[FileNode]:
        return iter(self._nodes[path] for path in sorted(self._nodes))

    def paths(self) -> list[str]:
        """All file paths in the graph, sorted."""
        return sorted(self._nodes)

    def get_state(self, path: str) -> FileState:
        """Current state of one file (SDD table 2: ``get_state``)."""
        return self.get(path).state

    def in_state(self, *states: FileState) -> list[str]:
        """Paths whose state is one of ``states``, sorted."""
        return [node.path for node in self if node.state in states]

    def neighbours(self, path: str) -> list[str]:
        """Direct neighbours: dependencies and dependents, sorted, without duplicates."""
        node = self.get(path)
        return sorted(set(node.dependencies) | set(node.dependents))

    def open_failures(self, path: str) -> list[str]:
        """Failure notes linked to a file: its pending fixes (see :meth:`add_pending_fix`)."""
        return list(self.get(path).pending_fixes)

    def counts(self) -> dict[str, int]:
        """Number of files per state, for ``migrate status`` and the log."""
        tally = Counter(node.state.value for node in self)
        return {state.value: tally.get(state.value, 0) for state in FileState}

    def status_lines(self) -> list[str]:
        """One ``state  path`` line per file, for ``migrate status``."""
        return [f"{node.state.value:<12} {node.path}" for node in self]

    # ------------------------------------------------------------------ writing
    def update_state(self, path: str, state: FileState) -> None:
        """Move a file to ``state`` and keep its call sites and notes consistent.

        ``EDITED`` marks the file's call sites patched, ``VERIFIED`` marks them verified,
        clears the pending fixes and writes a completion summary unless one is already
        set, and ``FAILED`` or ``ROLLED_BACK`` return them to pending so the file can be
        tried again.
        """
        node = self.get(path)
        node.state = FileState(state)
        status = _SITE_STATUS[node.state]
        for site in node.call_sites:
            site.status = status
        if node.state is FileState.VERIFIED:
            node.pending_fixes.clear()
            if not node.summary:
                node.summary = self._completion_note(node)

    def add_pending_fix(self, path: str, fix_id: str) -> None:
        """Record a fix that still has to be applied to ``path``."""
        node = self.get(path)
        if fix_id not in node.pending_fixes:
            node.pending_fixes.append(fix_id)

    def resolve_fix(self, path: str, fix_id: str) -> None:
        """Remove a fix once it has been applied; unknown identifiers are ignored."""
        node = self.get(path)
        if fix_id in node.pending_fixes:
            node.pending_fixes.remove(fix_id)

    def record_retry(self, path: str) -> int:
        """Count one repair attempt on ``path`` and return the new total."""
        node = self.get(path)
        node.retry_count += 1
        return node.retry_count

    def set_summary(self, path: str, summary: str) -> None:
        """Store a short note of completed work, cut to :data:`SUMMARY_LIMIT` characters."""
        text = " ".join(summary.split())
        if len(text) > SUMMARY_LIMIT:
            text = text[: SUMMARY_LIMIT - 1].rstrip() + "…"
        self.get(path).summary = text

    @staticmethod
    def _completion_note(node: FileNode) -> str:
        patterns = sorted({site.pattern_id for site in node.call_sites})
        done = f"{len(node.call_sites)} call site(s)"
        if patterns:
            done += f" ({', '.join(patterns)})"
        text = f"migrated {done}" if node.call_sites else "no call sites to migrate"
        if node.retry_count:
            text += f" after {node.retry_count} repair attempt(s)"
        return text[:SUMMARY_LIMIT]

    # ------------------------------------------------------------------ drift
    def _read(self, path: str) -> bytes | None:
        if self.repo_root is None:
            return None
        try:
            return (self.repo_root / path).read_bytes()
        except OSError:
            return None

    def refresh_hashes(self, paths: Iterable[str] | None = None) -> None:
        """Store the current content hash of ``paths`` (default: every file).

        Needs ``repo_root``. Files that cannot be read get an empty hash.
        """
        for path in list(paths) if paths is not None else self.paths():
            data = self._read(path)
            self.get(path).content_hash = hash_content(data) if data is not None else ""

    def drifted(self) -> list[str]:
        """Files whose content no longer matches the stored hash (needs ``repo_root``)."""
        changed = []
        for node in self:
            if not node.content_hash:
                continue
            data = self._read(node.path)
            if data is None or hash_content(data) != node.content_hash:
                changed.append(node.path)
        return changed

    # ------------------------------------------------------------------ persistence
    def to_dict(self) -> dict[str, Any]:
        """Plain-dict form of the whole graph (what :meth:`save` writes)."""
        return {
            "version": SCHEMA_VERSION,
            "nodes": [node.to_dict() for node in self],
            "unresolved": {
                path: [site.to_dict() for site in sites]
                for path, sites in sorted(self.unresolved.items())
            },
            "parse_failures": [failure.to_dict() for failure in self.parse_failures],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], repo_root: str | Path | None = None) -> GraphMemory:
        """Rebuild a graph from :meth:`to_dict` output; rejects other schema versions."""
        version = data.get("version")
        if version != SCHEMA_VERSION:
            raise ValueError(f"unsupported graph memory version {version!r}")
        memory = cls(repo_root)
        for raw in data.get("nodes", []):
            node = FileNode(
                path=raw["path"],
                state=FileState(raw.get("state", FileState.PENDING)),
                content_hash=raw.get("content_hash", ""),
                dependencies=list(raw.get("dependencies", [])),
                dependents=list(raw.get("dependents", [])),
                call_sites=[CallSite(**site) for site in raw.get("call_sites", [])],
                pending_fixes=list(raw.get("pending_fixes", [])),
                summary=raw.get("summary", ""),
                retry_count=int(raw.get("retry_count", 0)),
            )
            memory._nodes[node.path] = node
        for path, sites in data.get("unresolved", {}).items():
            memory.unresolved[path] = [
                UnresolvedSite(
                    file=site["file"],
                    line=site["line"],
                    expression=site["expression"],
                    reason=UnresolvedReason(site["reason"]),
                )
                for site in sites
            ]
        memory.parse_failures = [ParseFailure(**f) for f in data.get("parse_failures", [])]
        return memory

    def save(self, path: Path) -> None:
        """Write the whole graph to disk so a run can resume.

        The file is written to a temporary name and renamed, so an interrupted save
        never leaves a half-written memory file behind.
        """
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(json.dumps(self.to_dict(), indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)

    @classmethod
    def load(cls, path: Path, repo_root: str | Path | None = None) -> GraphMemory:
        """Read a graph written by :meth:`save`."""
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.from_dict(data, repo_root)
