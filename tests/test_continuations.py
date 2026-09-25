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

from tests.conftest import FIXTURES_DIR, fixture_sources

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
        # A new sentence is not a continuation, so it returns to column zero
        # even though it came out of a pinned line.
        "With a second sentence inside it.\n"
        "After the group.\n"
        "And more text after that too.\n"
    )


def test_pinned_break_ignores_min_chars() -> None:
    """Author intent outranks the heuristic that suppresses short segments."""
    source = "A long enough leading sentence here.\n  short bit.\n"
    assert _format(source, _opts(min_chars=100)) == source


def test_nesting_depth_is_flattened_to_one_level() -> None:
    """Indentation marks a continuation; it does not encode a depth.

    Anything indented is re-emitted at exactly one continuation indent, so a
    second level collapses onto the first rather than being preserved.
    """
    source = (
        "Intro clause here:\n"
        "  a first continuation,\n"
        "    a deeper one,\n"
        "  and back out again.\n"
    )
    assert _format(source, ON) == (
        "Intro clause here:\n"
        "  a first continuation,\n"
        "  a deeper one,\n"
        "  and back out again.\n"
    )


def test_sentences_return_to_column_zero_however_deeply_indented() -> None:
    """A new sentence is never a continuation, whatever the author indented."""
    source = (
        "Top level sentence here.\n"
        "  Level one continuation.\n"
        "    Level two continuation.\n"
        "  Back to level one.\n"
        "Back to top.\n"
    )
    assert _format(source, ON) == (
        "Top level sentence here.\n"
        "Level one continuation.\n"
        "Level two continuation.\n"
        "Back to level one.\n"
        "Back to top.\n"
    )


def test_a_pin_is_kept_where_the_break_would_not_come_back() -> None:
    """The indent is the only record of a break SemBr would not re-insert.

    Here the second line is not a sentence start by SemBr's own test — it is
    lower case — so collapsing would lose the break for good. The indent stays.
    """
    source = "Top level sentence here.\n  level one continuation.\n"
    assert _format(source, ON) == source


@pytest.mark.parametrize(
    "written",
    [" ", "\t", "   ", "    ", "        "],
    ids=["one-space", "tab", "three", "four", "eight"],
)
def test_any_indent_width_normalises_to_one_level(written: str) -> None:
    """Whatever the author wrote, the mark means "continuation" and no more."""
    source = f"A leading clause here,\n{written}a continuation line here.\n"
    assert _format(source, ON) == (
        f"A leading clause here,\n  a continuation line here.\n"
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


@pytest.mark.parametrize(
    "source",
    [
        pytest.param(
            "An intro with no clause punctuation at all\n"
            "  a pinned continuation of it.\n",
            id="no-preceding-punctuation",
        ),
        pytest.param(
            "An intro ending in a comma,\n  a pinned continuation of it.\n",
            id="preceding-punctuation",
        ),
    ],
)
def test_pinning_does_not_depend_on_punctuation(source: str) -> None:
    """Indentation is the signal; the punctuation before it is irrelevant."""
    assert _format(source, ON) == source


@pytest.mark.parametrize(
    "source",
    [
        pytest.param(
            "- level one, with a clause:\n  - level two, and another:\n"
            "    - level three, introducing:\n        a pinned continuation.\n",
            id="third-level-list-item",
        ),
        pytest.param(
            "> - A list inside a block quote:\n>     a pinned continuation.\n",
            id="list-in-block-quote",
        ),
        pytest.param(
            "- > A block quote inside a list item:\n  >   a pinned continuation.\n",
            id="block-quote-in-list",
        ),
        pytest.param(
            "> > A doubly nested quote:\n> >   a pinned continuation.\n",
            id="nested-block-quote",
        ),
    ],
)
def test_pinned_breaks_survive_at_depth(source: str) -> None:
    """Container indentation is stripped by the parser, so depth is irrelevant."""
    once = _format(source, ON)
    assert once == source
    assert is_md_equal(source, once, extensions={"sembr"}, options=ON)


def test_the_fixture_is_already_formatted_under_the_option() -> None:
    """The fixture doubles as documentation, so it must not merely survive.

    AST safety and idempotency would pass even if the option silently changed
    the fixture's shape. Asserting it comes back byte-for-byte is what keeps
    the file honest about the behaviour it illustrates.
    """
    source = (FIXTURES_DIR / "continuations.md").read_text(encoding="utf-8")
    assert _format(source, ON) == source
