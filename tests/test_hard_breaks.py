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
from mdformat._util import is_md_equal

from mdformat_sembr._sembr import insert_breaks

METADATA_BLOCK = "**Date:** 2026-08-02\\\n**Status:** Accepted\n"


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
