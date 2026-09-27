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
    "BREAK_MARK",
    "CONTINUATION_MARK",
    "DEFAULT_ABBREVIATIONS",
    "DEFAULT_CLAUSE_CHARS",
    "DEFAULT_CONTINUATION_INDENT",
    "DEFAULT_MIN_CHARS",
    "insert_breaks",
    "strip_marks",
]

# ---------------------------------------------------------------------------
# Defaults (ported from the original markdown-format.py hook script)
# ---------------------------------------------------------------------------

#: Minimum length of the segment preceding a break. Prevents splitting short
#: enumerations and fragments.
DEFAULT_MIN_CHARS = 15

#: Clause punctuation used by Iteration 2 (only when ``break_clauses`` is on).
DEFAULT_CLAUSE_CHARS = ",;:\u2014"  # comma, semicolon, colon, em dash

#: Spaces a continuation line is indented by, where indentation is switched on.
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

# A hard line break as mdformat renders it: a backslash, then the newline.
# (The two-trailing-spaces form has already been normalised to this by the
# time a paragraph postprocessor runs.)
#
# Backslashes before a newline pair up into literal backslashes, so the run
# has to be counted rather than merely inspected: an odd run ends in a hard
# break, an even one is a literal backslash followed by a soft break. A
# lookbehind cannot express that, because in ``\\\\\\`` the backslash before
# the last one is itself half of an escaped pair.
_BACKSLASH_RUN_RE = re.compile(r"\\+\n")
_HARD_BREAK = "\\\n"

#: Markers the plugin's break postprocessor writes to record that a newline
#: really was a line break in the source rather than word wrapping, and whether
#: the line it opens was indented — that is, whether it continues the line
#: above. Indentation is read as a yes-or-no signal, so there are two markers
#: and no notion of depth.
#:
#: A marker is written immediately *before* the break — ahead of a hard break's
#: own backslash, and never at the start of the line the break opens. mdformat's
#: paragraph renderer runs a line-start safety pass before postprocessors see
#: the text, escaping a leading ``#``, ``>``, ``-`` or ``1.`` so it cannot start
#: a block, and every one of those checks is anchored at column zero. A marker
#: sitting there would hide the real first character and silently suppress the
#: escape.
#:
#: Control characters are used because no Markdown construct produces one. They
#: are not impossible in a document, though, which is why a marker is only ever
#: recognised in the one position a marker can occupy — see :data:`_MARK_RE`.
#: NUL, the single character markdown-it guarantees to normalise away, is
#: unavailable: mdformat uses it for its own word-wrap markers.
BREAK_MARK = "\x01"
CONTINUATION_MARK = "\x02"

#: A marker only ever sits at the very end of a line: immediately before the
#: newline, or before the backslash the renderer writes after it for a hard
#: break. One of these characters anywhere else was in the document to begin
#: with, so it is left alone rather than stripped.
_MARK_RE = re.compile(rf"[{BREAK_MARK}{CONTINUATION_MARK}]+(?=\\?(?:\n|\Z))")
_TRAILING_MARK_RE = re.compile(rf"[{BREAK_MARK}{CONTINUATION_MARK}]+\Z")

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
# Continuation markers
# ---------------------------------------------------------------------------

def strip_marks(text: str) -> str:
    """Remove every marker, leaving plain Markdown behind."""
    return _MARK_RE.sub("", text)


def _take_mark(text: str) -> tuple[str, str | None]:
    """Split the marker off the end of ``text``, if there is one.

    A marker sits before the break it describes, so splitting on hard breaks
    strands it at the end of the run *preceding* the break even though it
    describes the line that break opens. Handing it back separately is what
    lets the next run start out knowing whether its first line is a
    continuation.
    """
    match = _TRAILING_MARK_RE.search(text)
    if not match:
        return text, None
    return text[: match.start()], match.group(0)[-1]


def _group_lines(text: str, *, continues: bool = False) -> list[str]:
    """Split one hard-break-free run into groups that are each reflowed alone.

    A group is a run of source lines that may be collapsed into each other. A
    marker ends the line a break closes and says whether the line that break
    opens was indented; ``continues`` says the same for the first line, whose
    marker was stranded on the far side of a hard break.

    An indented line continues the one above, so it is kept in a group of
    its own — and so is the line that returns to the margin after one, because
    hard wrapping never indents, so a break out of an indented line can only be
    one the author wrote. Between unindented lines, though, a break is exactly
    what hard wrapping produces, so those reflow together.

    Unmarked newlines are not line breaks the author wrote at all — they come
    from word wrapping or from an inline construct that spans lines — so they
    merge into the current group.
    """
    contents: list[str] = []
    marks: list[str | None] = []
    for line in text.split("\n"):
        content, mark = _take_mark(line)
        contents.append(content)
        marks.append(mark)

    # The break that opened a line is the marker closing the line before it.
    opening = [CONTINUATION_MARK if continues else BREAK_MARK, *marks[:-1]]

    groups: list[tuple[bool, list[str]]] = []
    for mark, content in zip(opening, contents):
        continuation = mark == CONTINUATION_MARK
        if groups and (
            # Not a line break at all: word wrapping, or an inline construct
            # that happens to span lines.
            mark is None
            # A break between two unindented lines, which is what a document
            # wrapped at a column width is made of.
            or (not continuation and not groups[-1][0])
        ):
            groups[-1][1].append(content)
            continue
        groups.append((continuation, [content]))

    return [" ".join(parts) for _, parts in groups]


# ---------------------------------------------------------------------------
# Core break logic
# ---------------------------------------------------------------------------

def _split_on_hard_breaks(text: str) -> list[str]:
    """Split ``text`` into runs separated by hard breaks."""
    segments: list[str] = []
    start = 0
    for match in _BACKSLASH_RUN_RE.finditer(text):
        if (len(match.group(0)) - 1) % 2 == 0:
            continue  # even run: literal backslashes and a soft break
        # End the segment before the backslash that forms the hard break.
        segments.append(text[start : match.end() - 2])
        start = match.end()
    segments.append(text[start:])
    return segments


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


def _is_sentence_break(previous: str, line: str, break_opts: dict) -> bool:
    """Return True if SemBr's sentence rule alone would re-insert this break.

    Asking the break logic itself, rather than re-deriving what a sentence
    boundary looks like, is what keeps the two from drifting apart: it accounts
    for the capitalised start the rule requires, for ``min_chars``, and for a
    break site inside a protected span such as link text, where no break is
    ever re-inserted. Clause breaking is switched off for the question however
    the caller configured it, because a clause break is exactly the kind that
    does not end the sentence.
    """
    before, after = previous.strip(), line.strip()
    if not before or not after:
        return False
    rejoined = _insert_breaks_between_hard_breaks(
        f"{before} {after}", **{**break_opts, "break_clauses": False}
    )
    return rejoined == f"{before}\n{after}"


def _indent_continuations(
    runs: list[list[str]],
    continuation_indent: int,
    break_opts: dict,
) -> None:
    """Indent every line that continues the sentence before it, in place.

    Indentation means "this line continues the one above", so it goes on every
    line that does — whether the break above it was a clause break, one the
    author wrote where no rule would have put one, or one word wrapping
    introduced. Only a line that starts a new sentence stays at the margin.

    Writing the indent is also what makes reading it reliable: a break the
    plugin could not re-derive survives the next run only because the line
    below it is indented, and so marked as a continuation.
    """
    if continuation_indent <= 0:
        return

    previous: str | None = None
    for run in runs:
        for index, line in enumerate(run):
            if previous is not None and not _is_sentence_break(
                previous, line, break_opts
            ):
                # Whatever the line arrived with is a floor, never a starting
                # point: mdformat's four-space HTML-block guard is already there.
                already = len(line) - len(line.lstrip(" "))
                line = " " * max(continuation_indent - already, 0) + line
            run[index] = line
            previous = line


def _break_run(text: str, *, continues: bool, **break_opts: object) -> list[str]:
    """Reflow one hard-break-free run into its lines.

    Grouping decides where the lines fall; :func:`_indent_continuations` decides
    afterwards which of them are indented.
    """
    lines: list[str] = []
    for content in _group_lines(text, continues=continues):
        # mdformat neutralises a line that could open an HTML block by indenting
        # it four spaces — the one construct it guards with whitespace instead
        # of a backslash. Collapsing the group would throw that away, so the
        # guard goes back on every line the group becomes.
        guard = " " * (len(content) - len(content.lstrip(" ")))

        body = _insert_breaks_between_hard_breaks(content, **break_opts)
        if not body:
            continue
        lines.extend(guard + line for line in body.split("\n"))

    return lines


def insert_breaks(
    text: str,
    *,
    min_chars: int = DEFAULT_MIN_CHARS,
    abbreviations: Iterable[str] | None = None,
    break_clauses: bool = False,
    clause_chars: str = DEFAULT_CLAUSE_CHARS,
    closing_punct: bool = False,
    continuation_indent: int = 0,  # off, so a direct caller opts in
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

    When ``continuation_indent`` is non-zero, a line that continues the one
    above is indented by that many spaces. Zero, the default, leaves the
    collapse-then-rebreak behaviour exactly as it was.

    Whether an *incoming* indented line is treated as a break to keep is not
    decided here: it depends on which marker the caller wrote (see
    :data:`CONTINUATION_MARK` and :func:`_group_lines`). Indenting the output
    and reading the input are separate choices, because a break the break rules
    can re-derive — a clause break, say — needs no marker to survive a round
    trip, while one they cannot re-derive survives only by being marked.
    """
    abbrev = (
        DEFAULT_ABBREVIATIONS
        if abbreviations is None
        else frozenset(abbreviations)
    )

    break_opts = {
        "min_chars": min_chars,
        "abbreviations": abbrev,
        "break_clauses": break_clauses,
        "clause_chars": clause_chars,
        "closing_punct": closing_punct,
    }

    runs: list[list[str]] = []
    continues = False
    for segment in _split_on_hard_breaks(text):
        segment, mark = _take_mark(segment)
        runs.append(_break_run(segment, continues=continues, **break_opts))
        continues = mark == CONTINUATION_MARK

    _indent_continuations(runs, continuation_indent, break_opts)
    return _HARD_BREAK.join("\n".join(run) for run in runs)


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
