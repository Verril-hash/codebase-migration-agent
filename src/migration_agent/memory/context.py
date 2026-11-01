"""Assemble a token-bounded prompt context for one file.

Owner: Prabanjan (Module 4). Design: SDD section 5.2: the target file, its
neighbours, open failures and summaries, cut to the token budget.

The context is built in priority order. The header, the call sites and the open
failures of the target are always kept; the target's source gets at most
:data:`SOURCE_SHARE` of what is left; neighbours and completed-work summaries
share the rest. The result never exceeds ``token_budget`` by :func:`estimate_tokens`.
"""

from __future__ import annotations

import math

from migration_agent.memory.graph_memory import GraphMemory
from migration_agent.models import FileNode, FileState

#: Rough characters per token for source code and English; no tokenizer dependency.
CHARS_PER_TOKEN = 4
#: Share of the remaining budget the target file's source may take.
SOURCE_SHARE = 0.6
#: Marker placed where text was cut.
TRUNCATED = "... [truncated]"
#: Lines of source kept around a call site, tried from the widest to the narrowest.
_RADII = (40, 20, 10, 5, 2, 0)


def estimate_tokens(text: str) -> int:
    """Estimate the token count of ``text`` (about four characters per token)."""
    return math.ceil(len(text) / CHARS_PER_TOKEN)


def _cut(text: str, tokens: int) -> str:
    """``text`` cut to at most ``tokens`` tokens, with a marker when something was removed."""
    if tokens <= 0:
        return ""
    if estimate_tokens(text) <= tokens:
        return text
    limit = tokens * CHARS_PER_TOKEN
    if limit <= len(TRUNCATED) + 1:
        return text[:limit]
    return text[: limit - len(TRUNCATED) - 1].rstrip() + "\n" + TRUNCATED


def _numbered(lines: list[str], keep: list[int]) -> str:
    """Lines at the 0-based indices in ``keep``, numbered, with ``...`` for each gap."""
    out: list[str] = []
    previous = -1
    for index in keep:
        if previous >= 0 and index != previous + 1:
            out.append("...")
        out.append(f"{index + 1:>5}  {lines[index]}")
        previous = index
    return "\n".join(out)


def _source_view(node: FileNode, source: str, tokens: int) -> str:
    """The file's source within ``tokens``: whole, then windows around call sites, then head."""
    lines = source.splitlines()
    full = _numbered(lines, list(range(len(lines))))
    if estimate_tokens(full) <= tokens:
        return full
    marked = sorted({site.line - 1 for site in node.call_sites if 0 < site.line <= len(lines)})
    if marked:
        for radius in _RADII:
            keep = sorted(
                {
                    i
                    for line in marked
                    for i in range(max(0, line - radius), min(len(lines), line + radius + 1))
                }
            )
            view = _numbered(lines, keep)
            if estimate_tokens(view) <= tokens:
                return view
        return _cut(_numbered(lines, marked), tokens)
    return _cut(full, tokens)


def _header(node: FileNode) -> str:
    return (
        f"## Target file: {node.path}\n"
        f"state: {node.state.value}; repair attempts so far: {node.retry_count}"
    )


def _sites_section(memory: GraphMemory, node: FileNode) -> str:
    lines = ["## Call sites to migrate"]
    for site in node.call_sites:
        lines.append(
            f"- line {site.line}, col {site.column}: {site.pattern_id} "
            f"in {site.enclosing_function} [{site.status}]"
        )
    for unresolved in memory.unresolved.get(node.path, []):
        lines.append(
            f"- line {unresolved.line}: could not resolve `{unresolved.expression}` "
            f"({unresolved.reason.value}); needs judgement"
        )
    if len(lines) == 1:
        lines.append("- none")
    return "\n".join(lines)


def _failures_section(node: FileNode) -> str:
    if not node.pending_fixes:
        return ""
    return "\n".join(["## Open failures"] + [f"- {fix}" for fix in node.pending_fixes])


def _neighbour_line(memory: GraphMemory, path: str, relation: str) -> str:
    other = memory.get(path)
    line = f"- {other.path} ({relation}, {other.state.value}"
    if other.call_sites:
        line += f", {len(other.call_sites)} call site(s)"
    line += ")"
    if other.summary:
        line += f": {other.summary}"
    return line


def _neighbours_section(memory: GraphMemory, node: FileNode) -> str:
    lines: list[str] = []
    for path in node.dependencies:
        if path in memory:
            lines.append(_neighbour_line(memory, path, "dependency"))
    for path in node.dependents:
        if path in memory:
            lines.append(_neighbour_line(memory, path, "dependent"))
    return "\n".join(["## Neighbouring files"] + lines) if lines else ""


def _completed_section(memory: GraphMemory, node: FileNode) -> str:
    near = set(node.dependencies) | set(node.dependents) | {node.path}
    done = [
        other
        for other in memory
        if other.summary and other.path not in near and other.state is FileState.VERIFIED
    ]
    if not done:
        return ""
    return "\n".join(["## Completed work"] + [f"- {o.path}: {o.summary}" for o in done])


def _read_source(memory: GraphMemory, path: str) -> str | None:
    if memory.repo_root is None:
        return None
    try:
        return (memory.repo_root / path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def build_context(memory: GraphMemory, path: str, token_budget: int) -> str:
    """Prompt context for ``path``, at most ``token_budget`` estimated tokens.

    Contains the target (state, call sites, open failures, source), its direct
    neighbours with their states and summaries, and a summary of work already
    verified elsewhere. Lower-priority parts are cut first. Raises ``KeyError`` for
    a file that is not in the graph.
    """
    node = memory.get(path)
    parts = [_header(node), _sites_section(memory, node), _failures_section(node)]
    head = "\n\n".join(part for part in parts if part)
    remaining = token_budget - estimate_tokens(head)
    if remaining <= 0:
        return _cut(head, token_budget)

    sections = [head]
    source = _read_source(memory, node.path)
    if source is None:
        sections.append("## Source\n(source not available to graph memory)")
    else:
        allowance = int(remaining * SOURCE_SHARE)
        overhead = estimate_tokens("\n\n## Source\n")
        view = _source_view(node, source, allowance - overhead)
        if view:
            sections.append("## Source\n" + view)
    remaining = token_budget - estimate_tokens("\n\n".join(sections))

    for build in (_neighbours_section, _completed_section):
        if remaining <= 0:
            break
        text = build(memory, node)
        if not text:
            continue
        text = _cut(text, remaining - estimate_tokens("\n\n"))
        if text:
            sections.append(text)
            remaining = token_budget - estimate_tokens("\n\n".join(sections))

    return _cut("\n\n".join(sections), token_budget)
