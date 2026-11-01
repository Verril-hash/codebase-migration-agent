"""Count deprecated usages independently of the matcher (sub-component 2.7).

Owner: Verril (Module 2). Design: Module 2 detailed design, class OracleDetector.

The completeness metric is (B - A) / B, where B and A are deprecated-usage
counts before and after migration. If the matcher produced those counts, a
call site it cannot see would be invisible to the metric too. So the oracle
uses a different technique and shares no code with the symbol table or the
matcher: text matching over the code with comments, strings and import
statements blanked out.

By default a pattern is counted wherever its final name appears in a file
that mentions the pattern's top-level package (``sqlalchemy``). Where the
final name is ambiguous (``execute`` exists on both the old ``Engine`` and the
new ``Connection``), give the pattern an explicit regular expression through
``regexes``; it is applied to the cleaned code.
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


class OracleDetector:
    def __init__(
        self,
        patterns: list[DeprecatedPattern],
        regexes: dict[str, str] | None = None,
        excludes: Iterable[str] = (),
    ) -> None:
        self.excludes = tuple(excludes)
        self.rules: dict[str, tuple[str, re.Pattern[str]]] = {}
        for pattern in patterns:
            root, _, _ = pattern.ast_matcher.partition(".")
            final = pattern.ast_matcher.rsplit(".", 1)[-1]
            source = (regexes or {}).get(
                pattern.pattern_id, rf"(?<!def )(?<!class )\b{re.escape(final)}\b"
            )
            self.rules[pattern.pattern_id] = (root, re.compile(source))

    def count_by_pattern(self, repo_path: str | Path) -> dict[str, int]:
        root = Path(repo_path)
        counts = dict.fromkeys(self.rules, 0)
        for relative in RepositoryScanner(root, self.excludes).scan():
            data = (root / relative).read_bytes()
            code = clean_code(data)
            if code is None:
                continue
            text = data.decode("utf-8", errors="replace")
            for pattern_id, (package, regex) in self.rules.items():
                if re.search(rf"\b{re.escape(package)}\b", text):
                    counts[pattern_id] += len(regex.findall(code))
        return counts

    def count(self, repo_path: str | Path) -> int:
        """Total deprecated usages in the repository."""
        return sum(self.count_by_pattern(repo_path).values())
