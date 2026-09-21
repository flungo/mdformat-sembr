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
    "CONTINUATION_MARK",
    "DEFAULT_MIN_CHARS",
    "DEFAULT_CLAUSE_CHARS",
    "DEFAULT_ABBREVIATIONS",
    "DEFAULT_CONTINUATION_INDENT",
    "continuation_mark",
    "insert_breaks",
    "strip_continuation_marks",
]

# ---------------------------------------------------------------------------
# Defaults (ported from the original markdown-format.py hook script)
# ---------------------------------------------------------------------------

#: Minimum length of the segment preceding a break. Prevents splitting short
#: enumerations and fragments.
DEFAULT_MIN_CHARS = 15

#: Clause punctuation used by Iteration 2 (only when ``break_clauses`` is on).
DEFAULT_CLAUSE_CHARS = ",;:\u2014"  # comma, semicolon, colon, em dash

#: Spaces per indentation level on a preserved continuation line.
DEFAULT_CONTINUATION_INDENT = 2

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

# Placeholder markers use NUL bytes which never occur in Markdown source text.
_PLACEHOLDER = "\x00{kind}{index}\x00"
_PLACEHOLDER_RE = re.compile(r"\x00([A-Z]+)(\d+)\x00")

#: Marker the plugin's break postprocessor writes to record that a newline
#: really was a line break in the source, and how deeply the line it opened was
#: indented. One mark means "not indented", each further mark one more level.
#:
#: It is written at the *end* of the line the break closes, never at the start
#: of the line it opens. mdformat's paragraph renderer runs a line-start safety
#: pass before postprocessors see the text — escaping a leading ``#``, ``>``,
#: ``-`` or ``1.`` so it cannot start a block — and every one of those checks is
#: anchored at column zero. A marker sitting there would hide the real first
#: character and silently suppress the escape.
#:
#: ``\x01`` cannot reach us from a document: markdown-it's ``normalize`` rule
#: rewrites NUL to U+FFFD, and no other control character survives parsing as
#: literal text. NUL itself is unavailable because mdformat uses it for its own
#: word-wrap markers.
CONTINUATION_MARK = "\x01"
_CONTINUATION_RE = re.compile(f"{CONTINUATION_MARK}+")
_CONTINUATION_TAIL_RE = re.compile(f"{CONTINUATION_MARK}+$")

# A CommonMark backslash hard break: an odd number of trailing backslashes, so
# that an escaped backslash ("\\\\") is not mistaken for one.
_HARD_BREAK_RE = re.compile(r"\\+$")

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

def continuation_mark(level: int) -> str:
    """Return the marker recording a line break onto a line at ``level``."""
    return CONTINUATION_MARK * (level + 1)


def strip_continuation_marks(text: str) -> str:
    """Remove every continuation marker, leaving plain Markdown behind."""
    return _CONTINUATION_RE.sub("", text)


def _ends_with_hard_break(line: str) -> bool:
    """Return True if ``line`` ends in a backslash hard break."""
    match = _HARD_BREAK_RE.search(line)
    return bool(match) and len(match.group(0)) % 2 == 1


def _group_lines(text: str) -> list[tuple[int, str]]:
    """Split marked paragraph text into ``(indent level, content)`` groups.

    A group is a run of source lines that may be collapsed into each other and
    reflowed. A marker ends the line a break closes and gives the indentation of
    the line that break opens: an indented one is pinned into a group of its
    own, and so is the line that de-dents back out of a pinned run, because the
    de-dent is itself structure. Consecutive unindented lines are the ordinary
    case and reflow together.

    Unmarked newlines are not line breaks the author wrote — they come from word
    wrapping or from an inline construct that spans lines — so they merge into
    the current group. A hard break always ends a group: collapsing one would
    change the output.
    """
    contents: list[str] = []
    levels: list[int | None] = []
    for line in text.split("\n"):
        match = _CONTINUATION_TAIL_RE.search(line)
        levels.append(len(match.group(0)) - 1 if match else None)
        contents.append(
            strip_continuation_marks(line[: match.start()] if match else line)
        )

    groups: list[tuple[int, list[str]]] = []
    for index, content in enumerate(contents):
        # The break that opened this line is the marker closing the line before.
        level = levels[index - 1] if index else None

        if not groups:
            start_level = 0
        elif _ends_with_hard_break(contents[index - 1]):
            # Stay at the enclosing indentation unless the break says otherwise.
            start_level = groups[-1][0] if level is None else level
        elif level is None or (level == 0 and groups[-1][0] == 0):
            groups[-1][1].append(content)
            continue
        else:
            start_level = level

        groups.append((start_level, [content]))

    return [(level, " ".join(parts)) for level, parts in groups]


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


def _break_group(
    text: str,
    *,
    min_chars: int,
    abbrev: frozenset[str],
    break_clauses: bool,
    clause_chars: str,
    closing_punct: bool,
) -> str:
    """Collapse one reflowable group and break it at SemBr boundaries."""
    collapsed = _collapse_whitespace(text)
    if not collapsed:
        return collapsed

    masked, store = _mask(collapsed)

    cut_points = _split_points(masked, abbrev, closing_punct)
    if break_clauses:
        cut_points += _clause_split_points(masked, clause_chars)

    broken = _apply_breaks(masked, cut_points, min_chars)
    return _unmask(broken, store)


def insert_breaks(
    text: str,
    *,
    min_chars: int = DEFAULT_MIN_CHARS,
    abbreviations: Iterable[str] | None = None,
    break_clauses: bool = False,
    clause_chars: str = DEFAULT_CLAUSE_CHARS,
    closing_punct: bool = False,
    continuation_indent: int = DEFAULT_CONTINUATION_INDENT,
) -> str:
    """Insert SemBr soft breaks into a single rendered paragraph string.

    Sentence boundaries (``.``/``!``/``?``) always break (Iteration 1). When
    ``break_clauses`` is true, clause punctuation in ``clause_chars`` also breaks
    (Iteration 2). Protected inline regions (code, links, images, footnote refs)
    and abbreviations are never split.

    Text carrying :data:`CONTINUATION_MARK` markers is first split into groups
    (see :func:`_group_lines`): breaks the author pinned by indenting the next
    line are preserved and re-emitted at ``continuation_indent`` spaces per
    level, and each group is reflowed independently. Unmarked text is one group,
    so the collapse-then-rebreak behaviour is unchanged.

    Only bare ``\\n`` soft breaks are emitted — never hard breaks. Rendered HTML
    output is therefore unchanged. The transform is deterministic and idempotent.
    """
    abbrev = (
        DEFAULT_ABBREVIATIONS
        if abbreviations is None
        else frozenset(abbreviations)
    )

    rendered: list[str] = []
    for level, content in _group_lines(text):
        # mdformat neutralises a line that could open an HTML block by indenting
        # it four spaces — the one construct it guards with whitespace instead
        # of a backslash. Collapsing would throw that away, so it is a floor on
        # what this group may be re-indented to.
        guard = len(content) - len(content.lstrip(" "))

        body = _break_group(
            content,
            min_chars=min_chars,
            abbrev=abbrev,
            break_clauses=break_clauses,
            clause_chars=clause_chars,
            closing_punct=closing_punct,
        )
        if not body:
            continue

        width = max(continuation_indent * level if continuation_indent > 0 else 0, guard)
        if width:
            pad = " " * width
            body = "\n".join(pad + line for line in body.split("\n"))
        rendered.append(body)

    return "\n".join(rendered)
