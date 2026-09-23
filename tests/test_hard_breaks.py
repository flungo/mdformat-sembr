"""Hard line breaks inside a paragraph are kept, not collapsed.

A hard break (backslash before the newline, or two trailing spaces, which
mdformat normalises to the backslash form before any paragraph postprocessor
runs) renders to ``<br>``. Collapsing its newline into a space turns it into a
literal backslash followed by a space, which renders differently — and since
the plugin declares ``CHANGES_AST = False``, mdformat's validator then refuses
to format the whole file.
"""

from __future__ import annotations

import time

import mdformat
import pytest
from markdown_it import MarkdownIt
from mdformat._util import is_md_equal

from mdformat_sembr._sembr import insert_breaks

METADATA_BLOCK = "**Date:** 2026-08-02\\\n**Status:** Accepted\n"

BACKSLASH = "A first sentence here.\\\nA second sentence here.\n"
TWO_SPACE = "A first sentence here.  \nA second sentence here.\n"

#: Both ways of writing a hard break. mdformat normalises the two-space
#: spelling to the backslash one, so every case ends up at ``BACKSLASH``
#: whichever it started as.
spellings = pytest.mark.parametrize(
    "src", [BACKSLASH, TWO_SPACE], ids=["backslash", "two-space"]
)


def test_backslash_hard_break_is_kept() -> None:
    src = "**Date:** 2026-08-02\\\n**Status:** Accepted"
    assert insert_breaks(src, min_chars=1) == src


def test_two_space_hard_break_is_kept_through_mdformat() -> None:
    src = "**Date:** 2026-08-02  \n**Status:** Accepted\n"
    out = mdformat.text(src, extensions={"sembr"})
    assert out == METADATA_BLOCK
    assert is_md_equal(src, out, extensions={"sembr"})


def test_sentences_either_side_of_a_hard_break_still_break() -> None:
    src = "First one. Second one.\\\nThird one. Fourth one."
    assert insert_breaks(src, min_chars=1) == (
        "First one.\nSecond one.\\\nThird one.\nFourth one."
    )


def test_escaped_backslash_at_end_of_line_is_a_soft_break() -> None:
    # ``\\`` is a literal backslash; the newline after it is soft and may be
    # collapsed like any other.
    src = "A literal backslash \\\\\nAnd more on the next line."
    assert insert_breaks(src, min_chars=1) == (
        "A literal backslash \\\\ And more on the next line."
    )


def test_hard_break_paragraph_is_ast_safe_in_a_list_item() -> None:
    src = "- **Date:** 2026-08-02\\\n  **Status:** Accepted\n"
    out = mdformat.text(src, extensions={"sembr"})
    assert out == src
    assert is_md_equal(src, out, extensions={"sembr"})


@spellings
def test_hard_break_is_ast_safe(src: str) -> None:
    out = mdformat.text(src, extensions={"sembr"})
    assert is_md_equal(src, out, extensions={"sembr"})


@spellings
def test_hard_break_is_normalised_but_kept(src: str) -> None:
    """mdformat writes hard breaks in backslash form; the break itself stays."""
    assert mdformat.text(src, extensions={"sembr"}) == BACKSLASH


@spellings
def test_hard_break_is_idempotent(src: str) -> None:
    once = mdformat.text(src, extensions={"sembr"})
    assert mdformat.text(once, extensions={"sembr"}) == once


# ---------------------------------------------------------------------------
# Runs of trailing backslashes
#
# Backslashes before a newline pair up into literal backslashes, so only the
# parity of the run decides what the newline is: odd ends in a hard break,
# even leaves a literal backslash and a soft break. Inspecting the character
# before the last backslash is not enough — in ``\\\`` that character is
# itself half of an escaped pair — so every length below is exercised.
# ---------------------------------------------------------------------------

BACKSLASH_RUNS = pytest.mark.parametrize("run", range(1, 7), ids=lambda n: f"{n}-bs")


def _paragraph_with_run(run: int) -> str:
    return "A first sentence here." + "\\" * run + "\nA second sentence here.\n"


@BACKSLASH_RUNS
def test_commonmark_treats_an_odd_run_as_a_hard_break(run: int) -> None:
    """The premise the split relies on, asserted against the parser itself."""
    html = MarkdownIt("commonmark").render(_paragraph_with_run(run))
    assert ("<br" in html) is (run % 2 == 1)


@BACKSLASH_RUNS
def test_only_an_odd_run_keeps_its_newline(run: int) -> None:
    src = _paragraph_with_run(run)
    out = mdformat.text(src, extensions={"sembr"})
    kept = "\\\n" in out
    assert kept is (run % 2 == 1), (
        f"{run} backslashes: newline was "
        f"{'kept' if kept else 'collapsed'} in {out!r}"
    )


@BACKSLASH_RUNS
def test_any_run_length_is_ast_safe(run: int) -> None:
    src = _paragraph_with_run(run)
    out = mdformat.text(src, extensions={"sembr"})
    assert is_md_equal(src, out, extensions={"sembr"})


@BACKSLASH_RUNS
def test_any_run_length_is_idempotent(run: int) -> None:
    once = mdformat.text(_paragraph_with_run(run), extensions={"sembr"})
    assert mdformat.text(once, extensions={"sembr"}) == once


def test_long_backslash_run_without_newline_stays_fast() -> None:
    """A backslash run that never reaches a newline must not backtrack.

    The earlier pattern took quadratic time here: about 6 s for 100 000
    backslashes. The current one finishes in milliseconds, so the bound below
    leaves a wide margin for slow machines.
    """
    src = "Start of the text. " + "\\" * 100_000 + " end."
    started = time.perf_counter()
    assert insert_breaks(src, min_chars=1) == src
    assert time.perf_counter() - started < 1.0


# ---------------------------------------------------------------------------
# Whitespace touching a hard break
#
# It is part of what the paragraph renders to: mdformat's validator treats
# "alpha <br>" and "alpha<br>" as different HTML and refuses the file, a
# non-breaking space is content, and mdformat indents a line that starts with
# HTML after a hard break so that it cannot open an HTML block. None of these
# paragraphs has a sentence boundary, so the plugin must not change them.
# ---------------------------------------------------------------------------

WHITESPACE_AT_HARD_BREAKS = pytest.mark.parametrize(
    "src",
    [
        "alpha \\\nbeta\n",
        "alpha   \\\nbeta\n",
        "alpha\t\\\nbeta\n",
        "alpha&nbsp;\\\nbeta\n",
        "First line here\n\\\nNext line here\n",
        "alpha\\\n    <div>bar</div>\n",
    ],
    ids=["space", "three-spaces", "tab", "nbsp", "backslash-alone", "indented-html"],
)


@WHITESPACE_AT_HARD_BREAKS
def test_whitespace_touching_a_hard_break_is_kept(src: str) -> None:
    out = mdformat.text(src, extensions={"sembr"})
    assert out == mdformat.text(src)
    assert is_md_equal(src, out, extensions={"sembr"})


@WHITESPACE_AT_HARD_BREAKS
def test_whitespace_touching_a_hard_break_is_idempotent(src: str) -> None:
    once = mdformat.text(src, extensions={"sembr"})
    assert mdformat.text(once, extensions={"sembr"}) == once


def test_sentences_still_break_next_to_kept_whitespace() -> None:
    src = "First one. Second one. \\\nThird one. Fourth one."
    assert insert_breaks(src, min_chars=1) == (
        "First one.\nSecond one. \\\nThird one.\nFourth one."
    )


# ---------------------------------------------------------------------------
# Interaction with the continuation options
#
# Preserving breaks marks each one, and a hard break's marker ends up just
# ahead of its own backslash, so it must not disturb the split above — and the
# indentation of the line a hard break opens has to survive being stranded on
# the far side of that split.
# ---------------------------------------------------------------------------

PRESERVE = {"plugin": {"sembr": {"preserve_indented_breaks": True}}}

option_states = pytest.mark.parametrize(
    "options",
    [
        pytest.param({}, id="default"),
        pytest.param(PRESERVE, id="preserve-indented-breaks"),
    ],
)


@BACKSLASH_RUNS
@option_states
def test_run_length_is_unaffected_by_preserving_breaks(
    run: int, options: dict
) -> None:
    src = _paragraph_with_run(run)
    out = mdformat.text(src, extensions={"sembr"}, options=options)
    assert ("\\\n" in out) is (run % 2 == 1)
    assert is_md_equal(src, out, extensions={"sembr"}, options=options)


@pytest.mark.parametrize(
    "src",
    [
        pytest.param(
            "Intro sentence here.\n  a kept line.\\\n  after the hard break.\n",
            id="kept-either-side",
        ),
        pytest.param(
            "A first sentence here.\\\n  a kept continuation.\n",
            id="kept-after",
        ),
        pytest.param(
            "Intro sentence here.\n  a kept line.\\\nA new sentence after it.\n",
            id="de-dent-after",
        ),
    ],
)
def test_indentation_survives_across_a_hard_break(src: str) -> None:
    out = mdformat.text(src, extensions={"sembr"}, options=PRESERVE)
    assert out == src
    assert is_md_equal(src, out, extensions={"sembr"}, options=PRESERVE)


INDENTING = pytest.mark.parametrize(
    "options",
    [
        pytest.param({"plugin": {"sembr": {"continuation_indent": 2}}}, id="indent"),
        pytest.param(PRESERVE, id="preserve-indented-breaks"),
    ],
)


@WHITESPACE_AT_HARD_BREAKS
@INDENTING
def test_whitespace_at_a_hard_break_survives_indenting(src: str, options: dict) -> None:
    """Either half rewrites the runs on both sides of a hard break, and with
    preservation on, the break's marker sits right beside that whitespace."""
    once = mdformat.text(src, extensions={"sembr"}, options=options)
    assert is_md_equal(src, once, extensions={"sembr"}, options=options)
    assert mdformat.text(once, extensions={"sembr"}, options=options) == once


@INDENTING
def test_an_empty_run_between_hard_breaks_gains_no_indent(options: dict) -> None:
    """Nothing sits between two consecutive hard breaks, so there is nothing to
    indent — even though the line after them is a continuation and is."""
    src = "A first line of prose here,\\\n\\\nand a continuation after two.\n"
    out = mdformat.text(src, extensions={"sembr"}, options=options)
    assert out.split("\n")[1] == "\\"
    assert out.split("\n")[2].startswith("  and")
    assert is_md_equal(src, out, extensions={"sembr"}, options=options)
    assert mdformat.text(out, extensions={"sembr"}, options=options) == out


@pytest.mark.parametrize(
    "src",
    [
        pytest.param("Hi. Yo.\\\nNext sentence here.\n", id="short-before"),
        pytest.param("Roses bloom.\\\nOk. Fine then.\n", id="short-after"),
        pytest.param("Roses bloom.\\\nSugar is sweet.\n", id="short-either-side"),
    ],
)
@INDENTING
def test_a_sentence_after_a_hard_break_stays_at_the_margin(
    src: str, options: dict
) -> None:
    """Whether a sentence starts at the hard break is the only question. The
    short sentences beside it break when min_chars is waived to ask it, and
    must not count against it."""
    assert mdformat.text(src, extensions={"sembr"}, options=options) == src


@INDENTING
def test_a_line_after_a_hard_break_is_indented_when_its_sentence_starts_later(
    options: dict,
) -> None:
    """Only the join is asked about, and no sentence starts there, however
    early one starts after it."""
    src = "Something continues here and goes on\\\nShort. Words here are many.\n"
    out = mdformat.text(src, extensions={"sembr"}, options=options)
    assert out == src.replace("\\\n", "\\\n  ")
