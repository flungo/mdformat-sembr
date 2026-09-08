"""Deterministic Semantic Line Break (sembr.org) insertion.

This module holds the pure, rule-based break logic. It is intentionally free of
any mdformat imports so it can be unit-tested in isolation and reused by the
plugin's paragraph postprocessor.

The sentence-boundary regex, abbreviation list, inline-code masking and
abbreviation guard are ported from the project's original
``.github/hooks/markdown-format/markdown-format.py`` script.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

__all__ = [
    "DEFAULT_MIN_CHARS",
    "DEFAULT_CLAUSE_CHARS",
    "DEFAULT_ABBREVIATIONS",
    "insert_breaks",
]

# ---------------------------------------------------------------------------
# Defaults (ported from the original markdown-format.py hook script)
# ---------------------------------------------------------------------------

#: Minimum length of the segment preceding a break. Prevents splitting short
#: enumerations and fragments.
DEFAULT_MIN_CHARS = 15

#: Clause punctuation used by Iteration 2 (only when ``break_clauses`` is on).
DEFAULT_CLAUSE_CHARS = ",;:\u2014"  # comma, semicolon, colon, em dash

#: Common abbreviations whose trailing dot must NOT end a sentence.
DEFAULT_ABBREVIATIONS: frozenset[str] = frozenset(
    {
        "e.g", "i.e", "etc", "vs", "cf", "viz", "al", "approx", "incl", "excl",
        "Mr", "Mrs", "Ms", "Dr", "Prof", "Sr", "Jr", "St",
        "Inc", "Ltd", "Co", "Corp", "U.S", "U.K", "U.N", "E.U",
        "Fig", "fig", "no", "No", "vol", "Vol", "ch", "Ch",
        "p", "pp", "para", "sec", "Sect",
    }
)

# ---------------------------------------------------------------------------
# Regexes
# ---------------------------------------------------------------------------

# Sentence-terminator (. ! ?) — including "?!"/"!?" runs — followed by
# whitespace and a likely sentence start. The negative lookbehind avoids
# breaking inside an ellipsis ("...").
_SENTENCE_BOUNDARY = re.compile(
    r'(?<=[.!?])(?<!\.\.\.)\s+(?=["\'(\[`*_]?[A-Z0-9])'
)

# Extended variant that also consumes an optional closing quote or bracket
# immediately after the terminator (American-English punctuation style, e.g.
# `"goodbye."` or `[sic.]`). Only used when ``closing_punct=True``.
_SENTENCE_BOUNDARY_CLOSING = re.compile(
    r'(?<=[.!?])(?<!\.\.\.)["\')\]]?\s+(?=["\'(\[`*_]?[A-Z0-9])'
)

# A hard line break as mdformat renders it: a backslash, then the newline.
# (The two-trailing-spaces form has already been normalised to this by the
# time a paragraph postprocessor runs.) A backslash that is itself escaped
# (``\\`` at end of line) is a literal backslash and a soft break, not a hard
# break, hence the lookbehind.
_HARD_BREAK_RE = re.compile(r"(?<!\\)\\\n")
_HARD_BREAK = "\\\n"

# Placeholder markers use NUL bytes which never occur in Markdown source text.
_PLACEHOLDER = "\x00{kind}{index}\x00"
_PLACEHOLDER_RE = re.compile(r"\x00([A-Z]+)(\d+)\x00")

# Protected inline constructs. Order matters: images/links before bare code so
# a link label containing backticks is masked as one unit.
_PROTECTED_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # Inline code spans: one or more backticks, no embedded newline.
    ("CODE", re.compile(r"`+[^`\n]*`+")),
    # Images and links: optional leading '!', label in [...], target in (...).
    ("LINK", re.compile(r"!?\[[^\]\n]*\]\([^)\n]*\)")),
    # Reference-style links / footnote references: [text][id] or [^id].
    ("REF", re.compile(r"!?\[[^\]\n]*\]\[[^\]\n]*\]")),
    ("FOOT", re.compile(r"\[\^[^\]\n]+\]")),
)


# ---------------------------------------------------------------------------
# Masking of protected regions
# ---------------------------------------------------------------------------

def _mask(text: str) -> tuple[str, dict[str, str]]:
    """Replace protected inline spans with opaque NUL-delimited tokens.

    Returns the masked text and a mapping of token -> original span, so the
    break regex can never match inside code, links, images or footnote refs.
    """
    store: dict[str, str] = {}
    counter = 0

    for kind, pattern in _PROTECTED_PATTERNS:

        def repl(m: re.Match[str], _kind: str = kind) -> str:
            nonlocal counter
            token = _PLACEHOLDER.format(kind=_kind, index=counter)
            store[token] = m.group(0)
            counter += 1
            return token

        text = pattern.sub(repl, text)

    return text, store


def _unmask(text: str, store: dict[str, str]) -> str:
    """Reverse :func:`_mask`, restoring original spans from placeholder tokens."""
    if not store:
        return text

    def repl(m: re.Match[str]) -> str:
        return store.get(m.group(0), m.group(0))

    # Repeat until stable in case a restored span contained another token
    # (protected spans never nest in practice, but this keeps it robust).
    prev = None
    while prev != text:
        prev = text
        text = _PLACEHOLDER_RE.sub(repl, text)
    return text


# ---------------------------------------------------------------------------
# Abbreviation guard
# ---------------------------------------------------------------------------

def _is_abbreviation_before(text: str, idx: int, abbreviations: frozenset[str]) -> bool:
    """Return True if the period just before ``idx`` belongs to an abbreviation."""
    j = idx - 1
    if j < 0 or text[j] != ".":
        return False
    start = j
    while start > 0 and (text[start - 1].isalnum() or text[start - 1] == "."):
        start -= 1
    token = text[start:j]  # word chars before the trailing dot
    return token in abbreviations


# ---------------------------------------------------------------------------
# Core break logic
# ---------------------------------------------------------------------------

def _collapse_whitespace(text: str) -> str:
    """Collapse all runs of whitespace (including newlines) to single spaces.

    Collapse-then-rebreak is what makes the transform deterministic and
    idempotent regardless of any existing soft breaks in the input.
    """
    return re.sub(r"\s+", " ", text).strip()


def _split_points(
    masked: str,
    abbreviations: frozenset[str],
    closing_punct: bool = False,
) -> list[int]:
    """Return sorted cut indices for sentence boundaries in ``masked`` text.

    Each index is the position of the whitespace run following a sentence
    terminator.  When ``closing_punct`` is True the extended regex is used,
    which also matches an optional closing quote or bracket before the
    whitespace (American-English punctuation style); the cut is then advanced
    past that closing character so it stays on the preceding line.
    """
    pattern = _SENTENCE_BOUNDARY_CLOSING if closing_punct else _SENTENCE_BOUNDARY
    points: list[int] = []
    for m in pattern.finditer(masked):
        if _is_abbreviation_before(masked, m.start(), abbreviations):
            continue
        if closing_punct:
            # Advance past any non-whitespace prefix (the closing char) so the
            # cut lands on the first whitespace character.
            prefix = m.group(0)
            ws_offset = 0
            while ws_offset < len(prefix) and not prefix[ws_offset].isspace():
                ws_offset += 1
            points.append(m.start() + ws_offset)
        else:
            points.append(m.start())
    return points


def _clause_split_points(masked: str, clause_chars: str) -> list[int]:
    """Return cut indices after independent-clause punctuation."""
    if not clause_chars:
        return []
    escaped = re.escape(clause_chars)
    # Clause punctuation followed by whitespace and a non-space continuation.
    pattern = re.compile(rf"(?<=[{escaped}])\s+(?=\S)")
    return [m.start() for m in pattern.finditer(masked)]


def _apply_breaks(masked: str, cut_points: Iterable[int], min_chars: int) -> str:
    """Insert newlines at ``cut_points`` subject to the ``min_chars`` threshold.

    The threshold is measured against the current line segment: a break is only
    inserted if the text since the previous break is at least ``min_chars`` long.
    """
    unique_points = sorted(set(cut_points))
    if not unique_points:
        return masked

    out: list[str] = []
    last = 0
    line_start = 0
    for point in unique_points:
        segment_len = len(masked[line_start:point].strip())
        if segment_len < min_chars:
            continue
        out.append(masked[last:point].rstrip())
        out.append("\n")
        # Skip the whitespace run that followed the boundary.
        next_start = point
        while next_start < len(masked) and masked[next_start].isspace():
            next_start += 1
        last = next_start
        line_start = next_start
    out.append(masked[last:])
    return "".join(out)


def insert_breaks(
    text: str,
    *,
    min_chars: int = DEFAULT_MIN_CHARS,
    abbreviations: Iterable[str] | None = None,
    break_clauses: bool = False,
    clause_chars: str = DEFAULT_CLAUSE_CHARS,
    closing_punct: bool = False,
) -> str:
    """Insert SemBr soft breaks into a single rendered paragraph string.

    Sentence boundaries (``.``/``!``/``?``) always break (Iteration 1). When
    ``break_clauses`` is true, clause punctuation in ``clause_chars`` also breaks
    (Iteration 2). Protected inline regions (code, links, images, footnote refs)
    and abbreviations are never split.

    Only bare ``\\n`` soft breaks are emitted — never hard breaks. Rendered HTML
    output is therefore unchanged. The transform is deterministic and idempotent.

    A hard break already in the paragraph (``\\`` before a newline) renders to
    ``<br>``, so it is kept exactly where it is: the text on either side of it
    is broken independently and the hard break itself is never collapsed.
    """
    abbrev = (
        DEFAULT_ABBREVIATIONS
        if abbreviations is None
        else frozenset(abbreviations)
    )

    return _HARD_BREAK.join(
        _insert_breaks_between_hard_breaks(
            segment,
            min_chars=min_chars,
            abbreviations=abbrev,
            break_clauses=break_clauses,
            clause_chars=clause_chars,
            closing_punct=closing_punct,
        )
        for segment in _HARD_BREAK_RE.split(text)
    )


def _insert_breaks_between_hard_breaks(
    text: str,
    *,
    min_chars: int,
    abbreviations: frozenset[str],
    break_clauses: bool,
    clause_chars: str,
    closing_punct: bool,
) -> str:
    """:func:`insert_breaks` for one run of text containing no hard break."""
    abbrev = abbreviations

    collapsed = _collapse_whitespace(text)
    if not collapsed:
        return collapsed

    masked, store = _mask(collapsed)

    cut_points = _split_points(masked, abbrev, closing_punct)
    if break_clauses:
        cut_points += _clause_split_points(masked, clause_chars)

    broken = _apply_breaks(masked, cut_points, min_chars)
    return _unmask(broken, store)
