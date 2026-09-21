"""Hard breaks must survive formatting, with or without the SemBr option set.

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

OPTIONS = [
    pytest.param({}, id="default"),
    pytest.param(
        {"plugin": {"sembr": {"preserve_indented_breaks": True}}},
        id="preserve-indented-breaks",
    ),
]


@pytest.mark.parametrize("options", OPTIONS)
@pytest.mark.parametrize("source", [BACKSLASH, TWO_SPACE], ids=["backslash", "two-space"])
def test_hard_break_is_ast_safe(source: str, options: dict) -> None:
    out = mdformat.text(source, extensions={"sembr"}, options=options)
    assert is_md_equal(source, out, extensions={"sembr"}, options=options)


@pytest.mark.parametrize("options", OPTIONS)
def test_hard_break_is_normalised_but_kept(options: dict) -> None:
    """mdformat writes hard breaks in backslash form; the break itself stays."""
    for source in (BACKSLASH, TWO_SPACE):
        assert mdformat.text(source, extensions={"sembr"}, options=options) == BACKSLASH


@pytest.mark.parametrize("options", OPTIONS)
def test_hard_break_is_idempotent(options: dict) -> None:
    once = mdformat.text(BACKSLASH, extensions={"sembr"}, options=options)
    assert mdformat.text(once, extensions={"sembr"}, options=options) == once


def test_hard_break_inside_a_pinned_continuation() -> None:
    options = {"plugin": {"sembr": {"preserve_indented_breaks": True}}}
    source = "Intro sentence here.\n  a pinned line.\\\n  after the hard break.\n"
    out = mdformat.text(source, extensions={"sembr"}, options=options)
    assert out == source
    assert is_md_equal(source, out, extensions={"sembr"}, options=options)
