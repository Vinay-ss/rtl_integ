"""Emacs-to-Python regex translation layer.

Converts Emacs Lisp regex syntax to Python ``re`` syntax so that
patterns lifted straight from ``verilog-mode.el`` work unchanged.
"""

from __future__ import annotations

import re
from typing import Optional

# ---------------------------------------------------------------------------
# Translation table — order matters (most-specific first)
# ---------------------------------------------------------------------------
_EMACS_TO_PYTHON: list[tuple[str, str]] = [
    # Emacs syntax classes
    (r"\s-", r"[ \t\n\f\r]"),
    (r"\s_", r"_"),
    (r"\sw", r"[A-Za-z0-9_]"),
    (r"\s.", r"[!-/:-@[-`{-~]"),
    # Word boundaries
    (r"\<", r"(?<![A-Za-z0-9_])(?=[A-Za-z0-9_])"),
    (r"\>", r"(?<=[A-Za-z0-9_])(?![A-Za-z0-9_])"),
    # Buffer anchors
    (r"\`", r"\A"),
    (r"\'", r"\Z"),
    # Alternation
    (r"\|", r"|"),
    # Grouping — \( \) become ( )
    (r"\(", r"("),
    (r"\)", r")"),
    # Word-constituent shortcuts
    (r"\w", r"[A-Za-z0-9_]"),
    (r"\W", r"[^A-Za-z0-9_]"),
]


def translate_emacs_regex(pattern: str) -> str:
    """Convert an Emacs-style regex *pattern* to Python ``re`` syntax.

    The translations are applied in priority order (see ``_EMACS_TO_PYTHON``).
    Literal parentheses in Emacs are ``(`` and ``)`` (no backslash).  In
    Python they need to become ``\\(`` and ``\\)``.
    """
    result: list[str] = []
    i = 0
    n = len(pattern)

    while i < n:
        matched = False

        # Try each Emacs escape sequence starting at position i
        if pattern[i] == "\\":
            for emacs_seq, py_seq in _EMACS_TO_PYTHON:
                elen = len(emacs_seq)
                if pattern[i: i + elen] == emacs_seq:
                    result.append(py_seq)
                    i += elen
                    matched = True
                    break

            if not matched:
                # Not a known Emacs escape — pass through as-is
                result.append(pattern[i])
                i += 1
        elif pattern[i] == "(":
            # Bare ( in Emacs is a literal paren → escape for Python
            result.append(r"\(")
            i += 1
        elif pattern[i] == ")":
            # Bare ) in Emacs is a literal paren → escape for Python
            result.append(r"\)")
            i += 1
        else:
            result.append(pattern[i])
            i += 1

    return "".join(result)


def emacs_re_compile(pattern: str, flags: int = 0) -> re.Pattern:
    """Translate an Emacs regex and compile it."""
    return re.compile(translate_emacs_regex(pattern), flags)


def string_match_fold(
    pattern: str, string: str, case_fold: bool = True
) -> Optional[re.Match]:
    """Equivalent of ``verilog-string-match-fold``.

    Matches *pattern* (Emacs syntax) against *string*.
    When *case_fold* is ``True`` the match is case-insensitive.
    """
    flags = re.IGNORECASE if case_fold else 0
    return emacs_re_compile(pattern, flags).search(string)
