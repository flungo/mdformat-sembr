"""Hard line breaks inside a paragraph are kept, not collapsed.

A hard break (backslash before the newline, or two trailing spaces, which
mdformat normalises to the backslash form before any paragraph postprocessor
runs) renders to ``<br>``. Collapsing its newline into a space turns it into a
literal backslash followed by a space, which renders differently — and since
the plugin declares ``CHANGES_AST = False``, mdformat's validator then refuses
to format the whole file.
"""

from __future__ import annotations

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


# ---------------------------------------------------------------------------
# Interaction with `preserve_indented_breaks`
#
# The marker that option writes sits immediately before a break, ahead of a
# hard break's own backslash, so it must not disturb the split above — and the
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
            "Intro sentence here.\n  a pinned line.\\\n  after the hard break.\n",
            id="pinned-either-side",
        ),
        pytest.param(
            "A first sentence here.\\\n  a pinned continuation.\n",
            id="pinned-after",
        ),
        pytest.param(
            "Intro sentence here.\n  a pinned line.\\\n  after the hard break.\n",
            id="continuation-after",
        ),
    ],
)
def test_indentation_survives_across_a_hard_break(src: str) -> None:
    out = mdformat.text(src, extensions={"sembr"}, options=PRESERVE)
    assert out == src
    assert is_md_equal(src, out, extensions={"sembr"}, options=PRESERVE)
