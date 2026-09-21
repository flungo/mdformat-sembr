"""Hard breaks must survive formatting.

A hard break is a soft break's opposite: its newline *is* the rendered output.
Collapsing one changes the HTML, which trips mdformat's ``is_md_equal``
validator and makes the CLI refuse to format the file at all.
"""

from __future__ import annotations

import mdformat
import pytest
from mdformat._util import is_md_equal

BACKSLASH = "A first sentence here.\\\nA second sentence here.\n"
TWO_SPACE = "A first sentence here.  \nA second sentence here.\n"


@pytest.mark.parametrize("source", [BACKSLASH, TWO_SPACE], ids=["backslash", "two-space"])
def test_hard_break_is_ast_safe(source: str) -> None:
    out = mdformat.text(source, extensions={"sembr"})
    assert is_md_equal(source, out, extensions={"sembr"})


def test_hard_break_is_normalised_but_kept() -> None:
    """mdformat writes hard breaks in backslash form; the break itself stays."""
    for source in (BACKSLASH, TWO_SPACE):
        assert mdformat.text(source, extensions={"sembr"}) == BACKSLASH


def test_hard_break_is_idempotent() -> None:
    once = mdformat.text(BACKSLASH, extensions={"sembr"})
    assert mdformat.text(once, extensions={"sembr"}) == once
