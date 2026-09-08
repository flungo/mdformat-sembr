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
from collections.abc import Iterable, Mapping

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
# whitespace and then something. The negative lookbehind avoids breaking
# inside an ellipsis ("..."). Whether what follows actually starts a sentence
# is decided by `_starts_sentence`, not by the regex: the text has been masked
# by then, so a sentence opening with a link or a code span begins with a
# placeholder, and a capital letter outside ASCII is a capital too.
#
# Emphasis and strikethrough closers (`*`, `_`, `~`) may sit between the
# terminator and the whitespace: "**A bold lead-in.** The rest." ends a
# sentence at the `**`, and there is no punctuation style in which it does
# not. They are consumed by the match and kept on the preceding line.
_SENTENCE_BOUNDARY = re.compile(r'(?<=[.!?])(?<!\.\.\.)[*_~]*\s+(?=\S)')

# Extended variant that also consumes closing quotes and brackets immediately
# after the terminator (American-English punctuation style, e.g. `"goodbye."`
# or `[sic.]`), in any combination with the emphasis closers above. Only used
# when ``closing_punct=True``.
_SENTENCE_BOUNDARY_CLOSING = re.compile(
    r'(?<=[.!?])(?<!\.\.\.)["\')\]*_~\u2019\u201d\u00bb]*\s+(?=\S)'
)

# Markup that may sit between the whitespace and the first letter of the next
# sentence: an opening quote, bracket, emphasis or code marker.
_OPENERS = "\"'([`*_\u201c\u2018\u00ab"

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
    terminator and any emphasis closers.  When ``closing_punct`` is True the
    extended regex is used, which also matches closing quotes and brackets
    before the whitespace (American-English punctuation style); closing
    characters of either kind stay on the preceding line.
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
        # Advance past any closing characters the match consumed so the cut
        # lands on the first whitespace character and they stay on the
        # preceding line.
        prefix = m.group(0)
        ws_offset = 0
        while ws_offset < len(prefix) and not prefix[ws_offset].isspace():
            ws_offset += 1
        points.append(m.start() + ws_offset)
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
    """
    abbrev = (
        DEFAULT_ABBREVIATIONS
        if abbreviations is None
        else frozenset(abbreviations)
    )

    collapsed = _collapse_whitespace(text)
    if not collapsed:
        return collapsed

    masked, store = _mask(collapsed)

    cut_points = _split_points(masked, abbrev, closing_punct, store)
    if break_clauses:
        cut_points += _clause_split_points(masked, clause_chars)

    broken = _apply_breaks(masked, cut_points, min_chars)
    return _unmask(broken, store)
