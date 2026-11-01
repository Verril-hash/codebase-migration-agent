"""Count deprecated usages independently of the matcher (sub-component 2.7).

Owner: Verril (Module 2). Design: Module 2 detailed design, class OracleDetector.

The completeness metric is (B - A) / B, where B and A are deprecated-usage
counts before and after migration. If the matcher produced those counts, a
call site it cannot see would be invisible to the metric too. So the oracle
uses a different technique and shares no code with the symbol table or the
matcher: text matching over the code with comments, strings and import
statements blanked out.

Each pattern is counted only in the files it can occur in (its *gate*), with a
rule that depends on its kind:

* A **method** (``sqlalchemy.orm.Session.query``, owner starts with a capital):
  attribute uses ``.query`` in files that use the top-level package. A plain
  variable called ``query`` is not counted.
* A **function that moved** (``sqlalchemy.ext.declarative.declarative_base``):
  uses of the name only in files that import from the *old* module. After
  migration the name is still used, but imported from the new place, so it
  correctly stops counting.

A file passes a gate if it matches it, or if it imports, directly or through
other files, a file that does. Imports are found with regular expressions,
not the symbol table.

Some names cannot be told apart as text: ``execute`` exists on the old
``Engine`` and on the new ``Connection`` and ``Session``. Give such a pattern
an explicit regular expression through ``regexes`` (the spec file's
``oracle_regexes``); it replaces the counting rule and is applied to the
cleaned code.
"""

from __future__ import annotations

import io
import re
import tokenize
from collections.abc import Iterable
from pathlib import Path

from migration_agent.analysis.scanner import RepositoryScanner
from migration_agent.models import DeprecatedPattern

_BLANKED = {tokenize.COMMENT, tokenize.STRING, tokenize.FSTRING_MIDDLE}
_STATEMENT_START = {tokenize.NEWLINE, tokenize.NL, tokenize.INDENT, tokenize.DEDENT}


def clean_code(data: bytes) -> str | None:
    """Source text with comments, string literals and import statements replaced by spaces.

    Line structure is kept. Returns None for a file that does not tokenize.
    """
    try:
        tokens = list(tokenize.tokenize(io.BytesIO(data).readline))
    except (tokenize.TokenError, SyntaxError):
        return None
    encoding = tokens[0].string if tokens and tokens[0].type == tokenize.ENCODING else "utf-8"
    lines = data.decode(encoding, errors="replace").splitlines(keepends=True)
    chars = [list(line) for line in lines]

    def blank(start: tuple[int, int], end: tuple[int, int]) -> None:
        for row in range(start[0], end[0] + 1):
            if row - 1 >= len(chars):
                break
            line = chars[row - 1]
            first = start[1] if row == start[0] else 0
            last = end[1] if row == end[0] else len(line)
            for col in range(first, min(last, len(line))):
                if line[col] not in "\r\n":
                    line[col] = " "

    at_statement_start = True
    import_start: tuple[int, int] | None = None
    for token in tokens:
        if token.type in _BLANKED:
            blank(token.start, token.end)
        if (
            token.type == tokenize.NAME
            and at_statement_start
            and token.string in {"import", "from"}
        ):
            import_start = token.start
        if token.type == tokenize.NEWLINE and import_start is not None:
            blank(import_start, token.start)
            import_start = None
        if token.type not in {tokenize.COMMENT, tokenize.ENCODING}:
            at_statement_start = token.type in _STATEMENT_START
    return "".join("".join(line) for line in chars)


#: One import statement per line; ``[ \t]`` (not ``\s``) so a match never runs into the next line.
_IMPORT_LINE = re.compile(
    r"^[ \t]*(?:from[ \t]+(\.*[\w.]*)[ \t]+import[ \t]+([\w \t,.*()]+)|import[ \t]+([\w., \t]+))",
    re.M,
)


def _suffix_index(files: set[str]) -> dict[str, set[str]]:
    """Every trailing path of every file -> the files ending with it (``app/db.py``, ``db.py``)."""
    index: dict[str, set[str]] = {}
    for file in files:
        parts = file.split("/")
        for start in range(len(parts)):
            index.setdefault("/".join(parts[start:]), set()).add(file)
    return index


def _imported_files(relative: str, text: str, index: dict[str, set[str]]) -> set[str]:
    """Repository files a file imports, found with regular expressions only.

    Independent of the symbol table on purpose; it may over-approximate, which
    only makes the oracle consider a few more files.
    """
    package = relative.rsplit("/", 1)[0] if "/" in relative else ""
    found: set[str] = set()
    for match in _IMPORT_LINE.finditer(text):
        source, names, plain = match.groups()
        modules: list[str] = []
        if plain:
            modules = [part.split(" as ")[0].strip() for part in plain.split(",")]
        elif source is not None:
            dots = len(source) - len(source.lstrip("."))
            base = source[dots:]
            if dots:
                parts = package.split("/") if package else []
                parts = parts[: len(parts) - (dots - 1)] if dots > 1 else parts
                prefix = "/".join(parts)
                base = f"{prefix.replace('/', '.')}.{base}" if base else prefix.replace("/", ".")
                base = base.strip(".")
            modules = [base] if base else []
            for name in re.split(r"[\s,()]+", names or ""):
                if name and name not in {"*", "as"} and base:
                    modules.append(f"{base}.{name}")
        for module in modules:
            path = module.replace(".", "/")
            for candidate in (f"{path}.py", f"{path}/__init__.py"):
                found |= index.get(candidate, set())
    found.discard(relative)
    return found


def default_rule(ast_matcher: str) -> tuple[str, str]:
    """``(gate, count)`` regular expressions for a pattern, by its kind (see module doc)."""
    parts = ast_matcher.split(".")
    final = re.escape(parts[-1])
    if len(parts) >= 2 and parts[-2][:1].isupper():  # Class.method
        return rf"\b{re.escape(parts[0])}\b", rf"\.{final}\b"
    module = ".".join(parts[:-1])
    parent, _, last = module.rpartition(".")
    gate = rf"\b{re.escape(module)}\b"
    if parent:  # also "from sqlalchemy.ext import declarative"
        gate += rf"|\bfrom[ \t]+{re.escape(parent)}[ \t]+import\b[^\n]*\b{re.escape(last)}\b"
    return gate, rf"(?<!def )(?<!class )\b{final}\b"


def _gated(gate: str, texts: dict[str, str]) -> set[str]:
    """Files that match ``gate``, plus files that import them, directly or not.

    ``users.py`` that only does ``from app.db import SessionLocal`` never mentions
    ``sqlalchemy`` but uses it all the same; this is the usual layout of real apps.
    """
    files = set(texts)
    mention = re.compile(gate)
    using = {f for f, text in texts.items() if mention.search(text)}
    index = _suffix_index(files)
    imports = {f: _imported_files(f, text, index) for f, text in texts.items()}
    changed = True
    while changed:
        changed = False
        for file in files - using:
            if imports[file] & using:
                using.add(file)
                changed = True
    return using


class OracleDetector:
    def __init__(
        self,
        patterns: list[DeprecatedPattern],
        regexes: dict[str, str] | None = None,
        excludes: Iterable[str] = (),
    ) -> None:
        self.excludes = tuple(excludes)
        #: pattern id -> (gate, counting regex)
        self.rules: dict[str, tuple[str, re.Pattern[str]]] = {}
        for pattern in patterns:
            gate, count = default_rule(pattern.ast_matcher)
            count = (regexes or {}).get(pattern.pattern_id, count)
            self.rules[pattern.pattern_id] = (gate, re.compile(count))

    def count_by_pattern(self, repo_path: str | Path) -> dict[str, int]:
        root = Path(repo_path)
        texts: dict[str, str] = {}
        codes: dict[str, str] = {}
        for relative in RepositoryScanner(root, self.excludes).scan():
            data = (root / relative).read_bytes()
            code = clean_code(data)
            if code is not None:
                texts[relative] = data.decode("utf-8", errors="replace")
                codes[relative] = code

        counts = dict.fromkeys(self.rules, 0)
        for pattern_id, (gate, regex) in self.rules.items():
            for relative in sorted(_gated(gate, texts)):
                counts[pattern_id] += len(regex.findall(codes[relative]))
        return counts

    def count(self, repo_path: str | Path) -> int:
        """Total deprecated usages in the repository."""
        return sum(self.count_by_pattern(repo_path).values())
