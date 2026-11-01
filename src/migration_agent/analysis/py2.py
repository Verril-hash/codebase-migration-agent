"""Fallback parser for Python 2 sources (sub-component 2.2).

Owner: Verril (Module 2). Design: Module 2 detailed design, figure 2: when the
primary ``ast`` parser rejects a file, try it as Python 2 before recording a
parse failure.

The Python 3 ``ast`` module cannot read Python 2 (``print "x"``,
``except E, e``). This fallback converts the source in memory with the
standard library's 2to3 fixers and parses the result. The converted tree is
used for **analysis only**: line numbers stay aligned with the original file,
so call sites point at the right lines, and edits are always made to the
original source.

``lib2to3`` ships with Python 3.12, the version this project pins. It was
removed in Python 3.13; there :data:`AVAILABLE` is False and Python 2 files are
reported as parse failures, as they would be without a fallback.
"""

from __future__ import annotations

import ast
import io
import tokenize
import warnings

try:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        warnings.simplefilter("ignore", PendingDeprecationWarning)
        from lib2to3 import refactor
        from lib2to3.pgen2.parse import ParseError
        from lib2to3.pgen2.tokenize import TokenError
    AVAILABLE = True
except ImportError:  # Python 3.13 and later
    AVAILABLE = False

_tool = None


def _refactoring_tool():
    global _tool
    if _tool is None:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            _tool = refactor.RefactoringTool(refactor.get_fixers_from_package("lib2to3.fixes"))
    return _tool


def python2_fallback(data: bytes, path: str) -> ast.Module:
    """Parse Python 2 source by converting it in memory.

    Raises ``SyntaxError`` when the text is not valid Python 2 either.
    """
    if not AVAILABLE:
        raise SyntaxError("Python 2 fallback needs lib2to3, which ships with Python 3.12")
    encoding, _ = tokenize.detect_encoding(io.BytesIO(data).readline)
    text = data.decode(encoding)
    if not text.endswith("\n"):
        text += "\n"
    try:
        converted = str(_refactoring_tool().refactor_string(text, path))
    except (ParseError, TokenError, IndentationError) as error:
        raise SyntaxError(f"not valid Python 2 either: {error}") from error
    return ast.parse(converted, filename=path)
