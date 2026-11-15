"""Deterministic Semantic Line Break (sembr.org) insertion.

This module holds the pure, rule-based break logic. It imports nothing from
mdformat, so it can be unit-tested in isolation and reused by the plugin's
paragraph postprocessor, and from markdown-it only the start conditions of an
HTML block.

The sentence-boundary regex, abbreviation list, inline-code masking and
abbreviation guard are ported from the project's original
``.github/hooks/markdown-format/markdown-format.py`` script.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping

from markdown_it.rules_block.html_block import HTML_SEQUENCES

__all__ = [
    "BREAK_MARK",
    "CONTINUATION_MARK",
    "DEFAULT_ABBREVIATIONS",
    "DEFAULT_CLAUSE_CHARS",
    "DEFAULT_CONTINUATION_INDENT",
    "DEFAULT_MIN_CHARS",
    "UNGUARDED_STARTS",
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
        "eg", "ie",  # undotted e.g./i.e., common in technical prose
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
# whitespace and then something. The negative lookbehind avoids breaking
# inside an ellipsis ("..."). Whether what follows actually starts a sentence
# is decided by `_starts_sentence`, not by the regex: the text has been masked
# by then, so a sentence opening with a link or a code span begins with a
# placeholder, and a capital letter outside ASCII is a capital too.
_SENTENCE_BOUNDARY = re.compile(r'(?<=[.!?])(?<!\.\.\.)\s+(?=\S)')

# Extended variant that also consumes an optional closing quote or bracket
# immediately after the terminator (American-English punctuation style, e.g.
# `"goodbye."` or `[sic.]`). Only used when ``closing_punct=True``.
_SENTENCE_BOUNDARY_CLOSING = re.compile(r'(?<=[.!?])(?<!\.\.\.)["\')\]]?\s+(?=\S)')

# Markup that may sit between the whitespace and the first letter of the next
# sentence: an opening quote, bracket, emphasis or code marker.
_OPENERS = "\"'([`*_\u201c\u2018\u00ab"

# A hard line break as mdformat renders it: a backslash, then the newline.
# (The two-trailing-spaces form has already been normalised to this by the
# time a paragraph postprocessor runs.)
#
# Backslashes before a newline pair up into literal backslashes, so the whole
# run has to be counted rather than only its last character inspected: an odd
# run ends in a hard break, an even one is literal backslashes followed by a
# soft break. (In a run of three, the backslash before the last one is itself
# half of an escaped pair.) The counting happens in _split_on_hard_breaks.
#
# Why ``(?<!\\)\\+\n`` rather than the simpler-looking ``\\+\n``: both find
# exactly the same runs, but Python's ``re`` backtracks. On n backslashes that
# are NOT followed by a newline, ``\\+\n`` is retried from every position inside
# the run, and each attempt backtracks through every shorter run before it
# fails: about n*n/2 steps (40 000 backslashes take ~1 s, a crafted 1 MB file
# several minutes). The negative lookbehind lets a match start only at the
# first backslash of a run, so each run is scanned once: linear time. No match
# is lost, because a match of ``\\+\n`` always starts at the beginning of a run
# anyway: if any tail of the run reaches the newline, the whole run does.
# (The possessive ``\\++\n`` would read better, but it needs Python 3.11 and
# this package supports 3.10.)
_BACKSLASH_RUN_RE = re.compile(r"(?<!\\)\\+\n")
_HARD_BREAK = "\\\n"

#: Markers :func:`insert_breaks` reads, when asked to, to learn that a newline
#: really was a line break in the source rather than part of an inline construct
#: that spans lines, and whether the line it opens was indented — that is,
#: whether it continues the line above. Indentation is read as a yes-or-no
#: signal, so there are two markers and no notion of depth.
#:
#: A marker ends the line a break closes: immediately before the newline, or
#: before the backslash of a hard break, so that the hard-break split never
#: sees it. Exactly one marker char ends a marked line, and the last line of a
#: run never carries one. The plugin marks no paragraph that already holds one
#: of these characters, so in marked text every one of them is a marker.
#:
#: Control characters are used because no Markdown construct produces one. NUL,
#: the single character markdown-it guarantees to normalise away, is unavailable:
#: mdformat uses it for its own word-wrap markers.
BREAK_MARK = "\x01"
CONTINUATION_MARK = "\x02"

_MARK_RE = re.compile(rf"[{BREAK_MARK}{CONTINUATION_MARK}](?=\\?\n)")
_TRAILING_MARK_RE = re.compile(rf"[{BREAK_MARK}{CONTINUATION_MARK}]\Z")

#: An HTML block can open on a paragraph's first line where it could never
#: interrupt one, which matters only when this module chooses where that line
#: ends. markdown-it's own start conditions, so the two cannot disagree.
_HTML_BLOCK_STARTS = tuple(opening for opening, _, _ in HTML_SEQUENCES)

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
    return text[: match.start()], match.group(0)


def _group_lines(
    text: str, *, continues: bool = False, marked: bool = False
) -> list[str]:
    """Split one hard-break-free run into groups that are each reflowed alone.

    A group is a run of source lines that may be collapsed into each other.
    Without ``marked`` nothing is known about where the author broke lines, so
    the whole run is one group, which is what collapse-and-reflow has always
    done.

    With it, a marker ends each line a break closes and says whether the line
    that break opens was indented; ``continues`` says the same for the first
    line, whose marker was stranded on the far side of a hard break. The last
    line closes no break within the run, so it carries no marker.

    An indented line continues the one above, so it is kept in a group of
    its own — and so is the line that returns to the margin after one, because
    hard wrapping never indents, so a break out of an indented line can only be
    one the author wrote. Between unindented lines, though, a break is exactly
    what hard wrapping produces, so those reflow together.

    An unmarked newline is not a line break the author wrote at all — it comes
    from mdformat's word wrapping, or from an inline construct that spans lines
    — so it merges into the current group.
    """
    lines = text.split("\n")
    if not marked:
        return [" ".join(lines)]

    contents: list[str] = []
    marks: list[str | None] = []
    for line in lines[:-1]:
        content, mark = _take_mark(line)
        contents.append(content)
        marks.append(mark)
    contents.append(lines[-1])

    # The break that opened a line is the marker closing the line before it.
    opening = [CONTINUATION_MARK if continues else BREAK_MARK, *marks]

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

    # An empty part is a line mdformat stripped to nothing, such as one that
    # held only a non-breaking space. Joined in, it would leave a leading space
    # that reads as the start of an HTML-block guard.
    return [" ".join(part for part in parts if part) for _, parts in groups]


def _opens_html_block(line: str) -> bool:
    """Return True if ``line``, starting a paragraph, would open an HTML block."""
    line = line.strip()
    return any(start.search(line) for start in _HTML_BLOCK_STARTS)


# ---------------------------------------------------------------------------
# Core break logic
# ---------------------------------------------------------------------------

#: Spaces, tabs, and everything ``str.splitlines`` ends a line at, which is
#: how mdformat's containers find the lines they prefix.
_LINE_WHITESPACE = " \t\n\r\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029"
_LINE_WHITESPACE_RE = re.compile(f"[{_LINE_WHITESPACE}]+")

#: Line starts that open a block that mdformat itself never escapes: a tilde
#: fence, and the markers of plugins that escape their own, but only in the
#: first column — mdformat-deflist's ``:`` and ``~``, mdformat-myst's ``%``
#: comment. Their parsers read such a marker however far it is indented, and
#: their escaping runs before or after this plugin's postprocessor as the
#: extensions are ordered. So a line starting with one is never indented, and
#: (see the plugin) never kept, which leaves it where those plugins look.
UNGUARDED_STARTS = "~:%"


def _non_space_count(text: str) -> int:
    """Return how many characters of ``text`` are not whitespace."""
    return sum(not char.isspace() for char in text)


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


def _collapse_whitespace(text: str, *, keep_unicode_spaces: bool = False) -> str:
    """Collapse all runs of whitespace (including newlines) to single spaces.

    Collapse-then-rebreak is what makes the transform deterministic and
    idempotent regardless of any existing soft breaks in the input.

    With ``keep_unicode_spaces``, only spaces, tabs and line ends are collapsed —
    the whitespace that can separate a block marker from what it opens, and
    that a container splits lines at — and a non-breaking space, say, is kept
    as the character it is.
    """
    if keep_unicode_spaces:
        return _LINE_WHITESPACE_RE.sub(" ", text).strip(_LINE_WHITESPACE)
    return re.sub(r"\s+", " ", text).strip()


def _starts_sentence(masked: str, i: int, store: Mapping[str, str]) -> bool:
    """True if ``masked[i:]`` reads as the start of a new sentence.

    That is an uppercase letter or a digit, optionally behind opening markup.
    A masked construct is judged by what it stands for: a code span routinely
    opens a sentence in technical prose, a link or image by the first letter
    of its label, and a footnote reference never does.
    """
    n = len(masked)
    while i < n and masked[i] in _OPENERS:
        i += 1
    if i >= n:
        return False
    if masked[i] == "\x00":
        m = _PLACEHOLDER_RE.match(masked, i)
        if m is None:
            return False
        kind, original = m.group(1), store.get(m.group(0), "")
        if kind == "CODE":
            return True
        if kind == "FOOT":
            return False
        # LINK / REF: strip the image bang and the opening bracket, then look
        # at the label the same way as plain text.
        return _starts_sentence(original.lstrip("!")[1:], 0, {})
    return masked[i].isupper() or masked[i].isdigit()


def _split_points(
    masked: str,
    abbreviations: frozenset[str],
    closing_punct: bool = False,
    store: Mapping[str, str] | None = None,
) -> list[int]:
    """Return sorted cut indices for sentence boundaries in ``masked`` text.

    Each index is the position of the whitespace run following a sentence
    terminator.  When ``closing_punct`` is True the extended regex is used,
    which also matches an optional closing quote or bracket before the
    whitespace (American-English punctuation style); the cut is then advanced
    past that closing character so it stays on the preceding line.
    ``store`` is the placeholder mapping from :func:`_mask`, consulted when the
    text after the boundary is a masked construct.
    """
    pattern = _SENTENCE_BOUNDARY_CLOSING if closing_punct else _SENTENCE_BOUNDARY
    points: list[int] = []
    for m in pattern.finditer(masked):
        if _is_abbreviation_before(masked, m.start(), abbreviations):
            continue
        if not _starts_sentence(masked, m.end(), store or {}):
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


def _apply_breaks(
    masked: str,
    cut_points: Iterable[int],
    min_chars: int,
    taken: list[int] | None = None,
) -> str:
    """Insert newlines at ``cut_points`` subject to the ``min_chars`` threshold.

    The threshold is measured against the current line segment: a break is only
    inserted if the text since the previous break is at least ``min_chars`` long.
    Each point a newline goes in at is appended to ``taken``, if given.
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
        if taken is not None:
            taken.append(point)
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

    Only the join itself is asked about. A rule that also breaks inside either
    line, as a lowered ``min_chars`` can, says nothing about whether a sentence
    starts at the join.
    """
    before, after = previous.strip(), line.strip()
    if not before or not after:
        return False
    rejoined = _insert_breaks_between_hard_breaks(
        f"{before} {after}", **{**break_opts, "break_clauses": False}
    )
    # A break only ever takes the place of whitespace, so counting everything
    # else finds where the first line ended.
    target = _non_space_count(before)
    seen = 0
    for broken in rejoined.split("\n")[:-1]:
        seen += _non_space_count(broken)
        if seen >= target:
            return seen == target
    return False


def _indent_continuations(
    runs: list[list[str]],
    starts: list[list[bool | None]],
    continuation_indent: int,
    break_opts: dict,
) -> None:
    """Indent every line that continues the sentence before it, in place.

    Indentation means "this line continues the one above", so it goes on every
    line that does — whether the break above it was a clause break, a hard
    break, or one the author wrote where no rule would have put one. Only a line
    that SemBr's sentence rule would break before stays at the margin, and a
    sentence too short for ``min_chars`` is not one.

    Writing the indent is also what makes reading it reliable: a break the
    plugin could not re-derive survives the next run only because the line
    below it is indented, and so marked as a continuation.

    Where the reflow made a break, ``starts`` says whether a sentence starts
    below it, and that answer stands: asked again of two lines out of context,
    the break logic can see a span or a ``min_chars`` count differently. Only a
    break the reflow did not make — a hard break, or one kept from the source
    — is put to it.
    """
    if continuation_indent <= 0:
        return
    if any(line.lstrip().startswith("|") for run in runs for line in run):
        # The paragraph postprocessor leaves alone a paragraph with a line that
        # starts with a pipe, taking it for a table no table plugin parsed, and
        # by then markdown-it has stripped the indentation from every line. So
        # the next run writes such a paragraph flush left, whatever this one
        # does, and this one writes it that way too.
        return

    # Across a hard break the line below stays a line whatever its indent says,
    # so the indent has no break to record there — only whether a sentence
    # starts, which is a question min_chars plays no part in.
    after_hard_break = {**break_opts, "min_chars": 0}

    previous: str | None = None
    for run, run_starts in zip(runs, starts):
        for index, (line, starts_sentence) in enumerate(zip(run, run_starts)):
            if not line.strip():
                # Nothing to continue: an empty run between two hard breaks, or
                # one holding only the whitespace that touches them.
                continue
            if line.lstrip()[0] in UNGUARDED_STARTS:
                previous = line
                continue
            if previous is not None and starts_sentence is None:
                starts_sentence = _is_sentence_break(
                    previous, line, after_hard_break if index == 0 else break_opts
                )
            if previous is not None and not starts_sentence:
                # Whatever the line arrived with is a floor, never a starting
                # point: mdformat's four-space HTML-block guard is already there.
                already = len(line) - len(line.lstrip(" "))
                line = " " * max(continuation_indent - already, 0) + line
            run[index] = line
            previous = line


#: Stands between two groups while finding the protected spans that cross
#: them. A marker character, so that it cannot be the document's own: a
#: paragraph already holding one is never grouped.
_GROUP_SEAM = BREAK_MARK + "{}" + BREAK_MARK
_GROUP_SEAM_RE = re.compile(rf"{BREAK_MARK}(\d+){BREAK_MARK}")


def _join_split_spans(groups: list[str], *, keep_unicode_spaces: bool) -> list[str]:
    """Join back the groups that a protected span runs across.

    Each group is masked on its own, so a link whose text holds a kept break
    would reach neither half whole: nothing would protect its title, or the
    text, from being broken. The paragraph as a whole is what the plugin
    masks without preservation, so that is what decides which spans there are,
    and a break inside one is not kept.
    """
    if len(groups) < 2:
        return groups
    parts = [
        _collapse_whitespace(group, keep_unicode_spaces=keep_unicode_spaces)
        for group in groups
    ]
    masked, _ = _mask("".join(
        part + (_GROUP_SEAM.format(i) if i < len(parts) - 1 else "")
        for i, part in enumerate(parts)
    ))
    # A seam swallowed into a placeholder fell inside a protected span.
    open_seams = {int(m.group(1)) for m in _GROUP_SEAM_RE.finditer(masked)}
    joined = [groups[0]]
    for i, group in enumerate(groups[1:]):
        if i in open_seams:
            joined.append(group)
        else:
            joined[-1] = f"{joined[-1]} {group}"
    return joined


def _break_run(
    text: str,
    *,
    continues: bool,
    marked: bool,
    opens_paragraph: bool,
    **break_opts: object,
) -> tuple[list[str], list[bool | None]]:
    """Reflow one hard-break-free run into its lines.

    Grouping decides where the lines fall; :func:`_indent_continuations` decides
    afterwards which of them are indented. Alongside the lines comes, for each,
    whether a sentence starts on it, wherever the reflow made the break above
    it and so knows; at the start of a group it does not, and that is None.
    """
    # A group that reflows to nothing, such as a line mdformat stripped to
    # nothing, writes no line, so it separates nothing either. It is collapsed
    # as the reflow below collapses it, so that the two agree on what is empty.
    groups = [
        group
        for group in _group_lines(text, continues=continues, marked=marked)
        if _collapse_whitespace(group, keep_unicode_spaces=marked)
    ]
    groups = _join_split_spans(groups, keep_unicode_spaces=marked)
    if opens_paragraph and len(groups) > 1 and _opens_html_block(groups[0]):
        # Keeping the break after a paragraph's first line would leave that line
        # on its own, and a line holding nothing but a tag opens an HTML block
        # wherever a paragraph would start, even though it could never interrupt
        # one. Collapsing a tag the source split across lines is how that line
        # comes about, so the break after it is not kept.
        groups[:2] = [f"{groups[0]} {groups[1]}"]

    lines: list[str] = []
    starts: list[bool | None] = []
    for index, content in enumerate(groups):
        sentence_starts: list[bool] = []
        body = _insert_breaks_between_hard_breaks(
            content, **break_opts, sentence_starts=sentence_starts
        )
        first, *rest = body.split("\n")
        if index:
            # mdformat neutralises a line that could open an HTML block by
            # indenting it four spaces — the one construct it guards with
            # whitespace instead of a backslash — and a kept group can start on
            # such a line. Collapsing the group throws that guard away, so it
            # goes back, on that line only: the lines reflow makes after it were
            # never at a line start, and an indent copied onto them would be
            # read back on the next run as a break the author kept. The first
            # group needs none: it opens either the paragraph, which mdformat
            # never guards, or the line after a hard break, whose whitespace
            # insert_breaks has already set aside.
            first = content[: len(content) - len(content.lstrip(" "))] + first
        lines.append(first)
        lines.extend(rest)
        starts.append(None)
        starts.extend(sentence_starts)

    return lines, starts


def insert_breaks(
    text: str,
    *,
    min_chars: int = DEFAULT_MIN_CHARS,
    abbreviations: Iterable[str] | None = None,
    break_clauses: bool = False,
    clause_chars: str = DEFAULT_CLAUSE_CHARS,
    closing_punct: bool = False,
    continuation_indent: int = 0,  # off, so a direct caller opts in
    marked: bool = False,
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
    Whitespace touching a hard break is kept verbatim too (see below).

    When ``continuation_indent`` is positive, a line that continues the one
    above is indented by that many spaces. Zero, the default, indents nothing.

    With ``marked``, ``text`` carries the markers :data:`BREAK_MARK` and
    :data:`CONTINUATION_MARK` at the ends of its lines, and an incoming indented
    line is kept as a break the author meant (see :func:`_group_lines`).
    Without it, no character in ``text`` is read as a marker. Indenting the
    output and reading the input are separate choices, because a break the
    break rules can re-derive — a clause break, say — needs no marker to
    survive a round trip, while one they cannot re-derive survives only by
    being marked.
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
        # A kept line starts where the author's line did, and mdformat checked
        # that line for block openers with its non-breaking spaces in place:
        # "-\u00a0x" opens no list, where "- x" would. Collapsing one into a
        # space there would open a block mdformat never saw.
        "keep_unicode_spaces": marked,
    }

    segments = _split_on_hard_breaks(text)
    last = len(segments) - 1
    runs: list[list[str]] = []
    starts: list[list[bool | None]] = []
    continues = False
    for i, segment in enumerate(segments):
        mark = None
        if marked and i < last:
            # A hard break's marker is the last thing before its backslash,
            # after any whitespace the line ends with, so it comes off first.
            segment, mark = _take_mark(segment)
        # Whitespace touching a hard break is part of what the paragraph
        # renders to, so it is set aside before the run is collapsed and put
        # back afterwards: a space before the backslash renders as "x <br>"
        # (mdformat's validator rejects "x<br>" as different HTML), a
        # non-breaking space is content, and mdformat indents a line that
        # starts with HTML after a hard break so that it cannot open an HTML
        # block. Only the whitespace inside a run is collapsed and re-broken.
        lead = trail = ""
        if i > 0:
            body = segment.lstrip()
            lead, segment = segment[: len(segment) - len(body)], body
        if i < last:
            body = segment.rstrip()
            trail, segment = segment[len(body) :], body
            if marked and "\n" in trail:
                # A soft break straight before this hard break, which therefore
                # sits alone on its line. That newline is kept with the rest of
                # the whitespace, and its marker describes only the hard break's
                # own empty line, so it goes.
                segment, _ = _take_mark(segment)
        run, run_starts = _break_run(
            segment,
            continues=continues,
            marked=marked,
            opens_paragraph=i == 0,
            **break_opts,
        )
        if not run:
            run, run_starts = [""], [None]
        run[0] = lead + run[0]
        run[-1] += trail
        runs.append(run)
        starts.append(run_starts)
        continues = mark == CONTINUATION_MARK

    _indent_continuations(runs, starts, continuation_indent, break_opts)
    return _HARD_BREAK.join("\n".join(run) for run in runs)


def _insert_breaks_between_hard_breaks(
    text: str,
    *,
    min_chars: int,
    abbreviations: frozenset[str],
    break_clauses: bool,
    clause_chars: str,
    closing_punct: bool,
    keep_unicode_spaces: bool = False,
    sentence_starts: list[bool] | None = None,
) -> str:
    """:func:`insert_breaks` for one run of text containing no hard break.

    For each line after the first it writes, ``sentence_starts``, if given, has
    appended whether the break above it was a sentence break.
    """
    abbrev = abbreviations

    collapsed = _collapse_whitespace(text, keep_unicode_spaces=keep_unicode_spaces)
    if not collapsed:
        return collapsed

    masked, store = _mask(collapsed)

    sentence_points = _split_points(masked, abbrev, closing_punct, store)
    cut_points = list(sentence_points)
    if break_clauses:
        cut_points += _clause_split_points(masked, clause_chars)

    taken: list[int] = []
    broken = _apply_breaks(masked, cut_points, min_chars, taken)
    if sentence_starts is not None:
        sentence_starts.extend(point in sentence_points for point in taken)
    return _unmask(broken, store)
