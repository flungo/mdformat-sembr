"""Tests for ``preserve_indented_breaks`` — optional SemBr breaks.

The SemBr specification's MAY rules (6, 8, 10, 11) describe breaks no formatter
can infer: they exist only because an author chose them. Collapse-and-reflow
destroys them. This option keeps the ones the author pinned by indenting the
line that follows, which is a signal the parser throws away but the plugin can
recover from token metadata.
"""

from __future__ import annotations

import mdformat
import pytest
from mdformat._util import is_md_equal

from tests.conftest import fixture_sources

#: The paragraph from the Universal Declaration of Human Rights that sembr.org
#: uses to introduce the convention, with the rule-6 break after "conscience"
#: (which no punctuation precedes) and a rule-8 grouped list.
REFERENCE = """\
All human beings are born free and equal in dignity and rights.
They are endowed with reason and conscience
  and should act towards one another in a spirit of brotherhood.
Another sentence here.
A list of items:
  the first item in the list;
  the second item in the list;
  and the final item of that list.
Before another sentence.
"""

ON: dict = {"plugin": {"sembr": {"preserve_indented_breaks": True}}}

#: Fixtures written with indented continuations. The option exists to change
#: these, so they are excluded from the "nothing moves" check below.
INDENTED_FIXTURES = {"continuations.md"}


def _opts(**overrides: object) -> dict:
    return {"plugin": {"sembr": {"preserve_indented_breaks": True, **overrides}}}


def _format(source: str, options: dict) -> str:
    return mdformat.text(source, extensions={"sembr"}, options=options)


def test_reference_paragraph_round_trips_unchanged() -> None:
    assert _format(REFERENCE, ON) == REFERENCE


def test_reference_paragraph_is_ast_safe_and_idempotent() -> None:
    once = _format(REFERENCE, ON)
    assert is_md_equal(REFERENCE, once, extensions={"sembr"}, options=ON)
    assert _format(once, ON) == once


def test_off_by_default_still_reflows_everything() -> None:
    out = mdformat.text(REFERENCE, extensions={"sembr"})
    assert "\n  " not in out
    assert "reason and conscience and should act" in out


def test_enabling_leaves_unindented_documents_untouched() -> None:
    """Existing SemBr-formatted documents have no indentation, so nothing moves."""
    for name, source in fixture_sources():
        if name in INDENTED_FIXTURES:
            continue
        baseline = mdformat.text(source, extensions={"sembr"})
        assert _format(source, ON) == baseline, f"fixture {name!r} changed"


def test_fixtures_stay_ast_safe_and_idempotent_with_the_option_on() -> None:
    for name, source in fixture_sources():
        once = _format(source, ON)
        assert is_md_equal(
            source, once, extensions={"sembr"}, options=ON
        ), f"AST changed for fixture {name!r}"
        assert _format(once, ON) == once, f"not idempotent for fixture {name!r}"


def test_accidental_hard_wrapping_is_still_reflowed() -> None:
    """A legacy document wrapped at a column width has no indentation to pin."""
    source = (
        "This document was hard wrapped at eighty columns by an older tool and\n"
        "therefore breaks in arbitrary places here.\n"
    )
    assert _format(source, ON) == (
        "This document was hard wrapped at eighty columns by an older tool "
        "and therefore breaks in arbitrary places here.\n"
    )


def test_pinned_group_is_still_reflowed_internally() -> None:
    """A pinned break stops a collapse; it does not stop SemBr breaking."""
    source = (
        "Intro clause here:\n"
        "  a pinned continuation. With a second sentence inside it.\n"
        "After the group. And more text after that too.\n"
    )
    assert _format(source, ON) == (
        "Intro clause here:\n"
        "  a pinned continuation.\n"
        "  With a second sentence inside it.\n"
        "After the group.\n"
        "And more text after that too.\n"
    )


def test_pinned_break_ignores_min_chars() -> None:
    """Author intent outranks the heuristic that suppresses short segments."""
    source = "A long enough leading sentence here.\n  short bit.\n"
    assert _format(source, _opts(min_chars=100)) == source


def test_nested_levels_are_preserved() -> None:
    source = (
        "Top level sentence here.\n"
        "  level one continuation.\n"
        "    level two continuation.\n"
        "  back to level one.\n"
        "back to top.\n"
    )
    assert _format(source, ON) == source


@pytest.mark.parametrize(
    ("written", "expected"),
    [
        (" ", "  "),  # one space is still one level
        ("\t", "  "),  # a tab counts as one column, so one level
        ("   ", "  "),  # an odd width snaps down to the level below
        ("    ", "    "),  # an exact multiple is two levels
    ],
)
def test_indent_width_is_normalised(written: str, expected: str) -> None:
    source = f"A leading sentence here.\n{written}a continuation line here.\n"
    assert _format(source, ON) == (
        f"A leading sentence here.\n{expected}a continuation line here.\n"
    )


def test_continuation_indent_is_configurable() -> None:
    source = "A leading sentence here.\n  a continuation line here.\n"
    assert _format(source, _opts(continuation_indent=4)) == (
        "A leading sentence here.\n    a continuation line here.\n"
    )


@pytest.mark.parametrize(
    "source",
    [
        pytest.param(
            "- An item sentence here.\n    a pinned continuation of the item.\n",
            id="list-item",
        ),
        pytest.param(
            "> A quoted sentence here.\n>   a pinned continuation of it.\n",
            id="block-quote",
        ),
        pytest.param(
            "1. An item sentence here.\n     a pinned continuation of it.\n",
            id="ordered-item",
        ),
        pytest.param(
            "A sentence here and now.\n  `code span` continuation line here.\n",
            id="code-span",
        ),
        pytest.param(
            "A sentence here and now.\n  [a link](http://example.com) continues.\n",
            id="link",
        ),
    ],
)
def test_pinned_breaks_survive_inside_containers(source: str) -> None:
    once = _format(source, ON)
    assert once == source
    assert is_md_equal(source, once, extensions={"sembr"}, options=ON)


@pytest.mark.parametrize("wrap", ["keep", "no", 30, 60], ids=str)
def test_pinned_breaks_outrank_word_wrapping(wrap: object) -> None:
    """SemBr owns the line structure, so ``--wrap N`` must not move a pinned break."""
    source = (
        "Intro sentence at the top here.\n"
        "  a pinned continuation that is quite a lot longer than the wrap width.\n"
        "Back at the top level again.\n"
    )
    options = {**ON, "wrap": wrap}
    once = _format(source, options)
    assert once == source
    assert is_md_equal(source, once, extensions={"sembr"}, options=options)


def test_marker_never_reaches_the_output() -> None:
    """The sentinel is internal; a stray one would corrupt the document."""
    out = _format(REFERENCE, ON)
    assert "\x01" not in out


def test_headings_are_not_indented() -> None:
    """A setext heading folds to one line; a continuation marker there would leak."""
    source = "A heading here\nsecond line\n===\n"
    assert _format(source, ON) == "# A heading here second line\n"


def test_tables_are_left_alone() -> None:
    source = "| Col A | Col B |\n| ----- | ----- |\n| one | two |\n"
    once = _format(source, ON)
    assert "\x01" not in once
    assert is_md_equal(source, once, extensions={"sembr"}, options=ON)
