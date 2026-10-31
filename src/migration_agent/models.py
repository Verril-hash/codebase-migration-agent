"""Shared data shapes: the contract between all nine modules.

Every module reads and writes these types and nothing else, so the four of us
can build in parallel. The shapes come from the System Design Document,
section 6 (data design), and the Module 2 detailed design, section 6.1.

Rule: a field is added or renamed only in a pull request that every owner
reviews, because every module depends on this file.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class FileState(StrEnum):
    """Migration state of one source file (SDD figure 5)."""

    PENDING = "pending"
    ANALYSED = "analysed"
    EDITED = "edited"
    VERIFIED = "verified"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


class EdgeType(StrEnum):
    IMPORT = "import"
    CALL = "call"
    INHERIT = "inherit"


class Strategy(StrEnum):
    """How a replacement rule is applied (SDD section 6.2)."""

    CODEMOD = "codemod"
    LLM = "llm"


class UnresolvedReason(StrEnum):
    """Why a call could not be resolved statically (Module 2 design, table 5)."""

    DYNAMIC_ATTRIBUTE = "dynamic_attribute"
    DYNAMIC_CALL = "dynamic_call"
    UNKNOWN_RECEIVER = "unknown_receiver"
    STAR_IMPORT = "star_import_unresolved"


class Shape:
    """Mixin: every shape serialises to a plain dict for logs and JSON files."""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)  # type: ignore[call-overload]


# ----------------------------------------------------------------- Module 1: specification
@dataclass
class DeprecatedPattern(Shape):
    pattern_id: str
    #: Fully qualified name or dotted pattern, e.g. "sqlalchemy.orm.Session.query".
    ast_matcher: str
    description: str = ""


@dataclass
class ReplacementRule(Shape):
    pattern_id: str
    strategy: Strategy
    #: Codemod name for Strategy.CODEMOD, prompt template name for Strategy.LLM.
    ref: str


@dataclass
class MigrationSpec(Shape):
    spec_id: str
    title: str
    target_version: str
    deprecated_patterns: list[DeprecatedPattern] = field(default_factory=list)
    replacement_rules: list[ReplacementRule] = field(default_factory=list)
    #: Name of the oracle detector that scores completeness for this migration.
    oracle: str = ""


@dataclass
class RunConfig(Shape):
    repo_path: str
    spec_path: str
    token_budget: int = 200_000
    max_steps: int = 200
    max_retries: int = 3
    batch_size: int = 5
    impact_depth_limit: int = 5
    dry_run: bool = False


# ----------------------------------------------------------------- Module 2: analysis
@dataclass
class CallSite(Shape):
    file: str
    line: int
    column: int
    enclosing_function: str
    pattern_id: str
    resolved: bool = True
    status: str = "pending"  # pending | patched | verified


@dataclass
class UnresolvedSite(Shape):
    file: str
    line: int
    expression: str
    reason: UnresolvedReason


@dataclass
class ParseFailure(Shape):
    file: str
    reason: str


@dataclass
class Edge(Shape):
    source: str
    target: str
    type: EdgeType
    symbol: str = ""


@dataclass
class ImpactSet(Shape):
    functions: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    #: function -> depth from its nearest call site
    depth: dict[str, int] = field(default_factory=dict)


@dataclass
class AnalysisResult(Shape):
    #: file -> files it depends on
    import_graph: dict[str, list[str]] = field(default_factory=dict)
    #: qualified function -> functions it calls
    call_graph: dict[str, list[str]] = field(default_factory=dict)
    call_sites: list[CallSite] = field(default_factory=list)
    impact_set: ImpactSet = field(default_factory=ImpactSet)
    unresolved_sites: list[UnresolvedSite] = field(default_factory=list)
    parse_failures: list[ParseFailure] = field(default_factory=list)


# ----------------------------------------------------------------- Module 3: planning
@dataclass
class Batch(Shape):
    batch_id: int
    files: list[str]


@dataclass
class BatchPlan(Shape):
    batches: list[Batch] = field(default_factory=list)


# ----------------------------------------------------------------- Module 4: memory
@dataclass
class FileNode(Shape):
    """One node of the graph memory, keyed by path (SDD table 3)."""

    path: str
    state: FileState = FileState.PENDING
    content_hash: str = ""
    dependencies: list[str] = field(default_factory=list)
    dependents: list[str] = field(default_factory=list)
    call_sites: list[CallSite] = field(default_factory=list)
    pending_fixes: list[str] = field(default_factory=list)
    summary: str = ""
    retry_count: int = 0


# ----------------------------------------------------------------- Module 5: editing
@dataclass
class Patch(Shape):
    file: str
    #: Unified diff for this one file.
    diff: str
    source: Strategy


# ----------------------------------------------------------------- Module 6: verification
@dataclass
class TestFailure(Shape):
    __test__ = False  # not a pytest test class, despite the name

    test_id: str
    message: str
    traceback: str = ""


@dataclass
class TestReport(Shape):
    __test__ = False  # not a pytest test class, despite the name

    passed: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0
    failures: list[TestFailure] = field(default_factory=list)
    lint_findings: list[str] = field(default_factory=list)

    @property
    def green(self) -> bool:
        return self.failed == 0 and self.errors == 0


# ----------------------------------------------------------------- Module 7: correction
@dataclass
class FixRequest(Shape):
    file: str
    function: str
    failure: TestFailure
    reason: str


# ----------------------------------------------------------------- Module 8: logging
@dataclass
class LogEntry(Shape):
    """One step of the trajectory log (SDD section 6.3)."""

    step_id: int
    timestamp: str
    module: str
    action: str
    batch_id: int | None = None
    files: list[str] = field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    status: str = "ok"
    detail: dict[str, Any] = field(default_factory=dict)


# ----------------------------------------------------------------- Module 9: evaluation
@dataclass
class TaskResult(Shape):
    task_id: str
    completeness: float  # % of deprecated usages migrated (oracle based)
    test_pass_rate: float  # % of post-migration tests passing
    tokens: int
    steps: int
    verdict: str  # green | partial | failed
