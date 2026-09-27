"""Tests for indented continuation lines — the two halves of one convention.

``continuation_indent`` writes it: a line that continues the one above is
indented. ``preserve_indented_breaks`` reads it: a line the author indented is
one they broke on purpose, so the break is kept rather than reflowed away.

They are deliberately separate opt-ins, and the distinction is which breaks
survive a round trip on their own. A clause break is recovered from its
punctuation every run, so indenting it needs no reading half — and deleting the
punctuation must still delete the break, indent and all. The SemBr
specification's MAY rules (6, 8, 10, 11) are the opposite case: no formatter can
infer them, so they survive only by being marked, which is what the reading half
is for.

The signal is indentation, which the parser throws away and the plugin recovers
from token metadata.
"""

from __future__ import annotations

import warnings

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

#: The width used throughout, which is also the width the README recommends.
INDENT = 2

#: Both halves. The width is left unset deliberately, so these tests also cover
#: ``preserve_indented_breaks`` raising the default off zero — it has nowhere to
#: record a break it keeps unless the line below is indented.
ON: dict = {"plugin": {"sembr": {"preserve_indented_breaks": True}}}

#: Writing only: continuations are indented on the way out, and indentation on
#: the way in means nothing.
INDENT_ONLY: dict = {"plugin": {"sembr": {"continuation_indent": INDENT}}}

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


@pytest.mark.parametrize(
    "options",
    [
        pytest.param({}, id="unset"),
        # Indenting alone never reads the indentation it writes.
        pytest.param(INDENT_ONLY, id="indent-only"),
    ],
)
def test_reading_off_still_reflows_everything(options: dict) -> None:
    out = mdformat.text(REFERENCE, extensions={"sembr"}, options=options)
    assert "\n  " not in out
    assert "reason and conscience and should act" in out


# ---------------------------------------------------------------------------
# The one contradictory configuration
#
# Preserving records a kept break by indenting the line below it, and that
# indent is also how the next run recognises it. At zero width there is nothing
# to write and so nothing to read back: preserving would emit a document the
# next run undoes. So preservation is off — and said out loud, because silence
# would leave an author believing their breaks are protected.
# ---------------------------------------------------------------------------


def test_a_zero_width_turns_preserving_off_rather_than_breaking_the_round_trip(
) -> None:
    options = _opts(continuation_indent=0)
    with pytest.warns(UserWarning, match="preserve_indented_breaks is ignored"):
        out = _format(REFERENCE, options)
    # Identical to the plugin's own defaults: nothing preserved, nothing indented.
    assert out == mdformat.text(REFERENCE, extensions={"sembr"})
    with pytest.warns(UserWarning):
        assert _format(out, options) == out


@pytest.mark.parametrize(
    "options",
    [
        pytest.param({}, id="unset"),
        pytest.param(INDENT_ONLY, id="indent-only"),
        pytest.param(ON, id="preserve"),
        pytest.param(_opts(continuation_indent=4), id="preserve-wide"),
        pytest.param(
            {"plugin": {"sembr": {"continuation_indent": 0}}}, id="explicit-zero-alone"
        ),
    ],
)
def test_workable_configurations_are_silent(options: dict) -> None:
    """Only the contradiction warns. A zero width on its own is just "off"."""
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        _format(REFERENCE, options)


@pytest.mark.parametrize(
    "options",
    [pytest.param(ON, id="preserve"), pytest.param(INDENT_ONLY, id="indent-only")],
)
def test_enabling_leaves_unindented_documents_untouched(options: dict) -> None:
    """Existing SemBr-formatted documents have no indentation, so nothing moves.

    True of either half, and for different reasons. Reading finds nothing to
    keep, because this plugin's own output is flush left. Writing finds nothing
    to indent, because that output breaks only where a sentence starts — the one
    place a continuation indent never goes.
    """
    for name, source in fixture_sources():
        if name in INDENTED_FIXTURES:
            continue
        baseline = mdformat.text(source, extensions={"sembr"})
        assert _format(source, options) == baseline, f"fixture {name!r} changed"


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
    expected = "A leading clause here,\n  a continuation line here.\n"
    assert _format(source, ON) == expected


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
        # These two open with inline markup, so the line before them ends in a
        # comma rather than a full stop: what is under test is that the break
        # survives in this container, and a continuation that could be read as
        # a new sentence would instead be testing sentence-start detection.
        pytest.param(
            "A clause here and now,\n  `code span` continuation line here.\n",
            id="code-span",
        ),
        pytest.param(
            "A clause here and now,\n  [a link](http://example.com) continues.\n",
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
    """The sentinels are internal; a stray one would corrupt the document."""
    out = _format(REFERENCE, ON)
    assert "\x01" not in out
    assert "\x02" not in out


def test_a_control_character_in_the_source_is_left_alone() -> None:
    """The sentinels are control characters, so a literal one must survive.

    Only a marker's position — the very end of a line — is stripped, so a
    control character the author put in the text stays where it is rather than
    being silently deleted.
    """
    source = "A sentence with a \x01 literal control character in it.\n"
    assert _format(source, ON) == source


def test_headings_are_not_indented() -> None:
    """A setext heading folds to one line; a continuation marker there would leak."""
    source = "A heading here\nsecond line\n===\n"
    assert _format(source, ON) == "# A heading here second line\n"


def test_tables_are_left_alone() -> None:
    source = "| Col A | Col B |\n| ----- | ----- |\n| one | two |\n"
    once = _format(source, ON)
    assert "\x01" not in once
    assert "\x02" not in once
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


# ---------------------------------------------------------------------------
# Clause breaks
#
# A clause break does not end the sentence, so the line it opens continues the
# one above and is indented like any other continuation. Only a new sentence
# returns to the margin.
# ---------------------------------------------------------------------------

CLAUSES = (
    "A leading clause that is long enough, and a continuation of it. "
    "A new sentence here.\n"
)

CLAUSES_BROKEN = (
    "A leading clause that is long enough,\n"
    "and a continuation of it.\n"
    "A new sentence here.\n"
)

CLAUSES_INDENTED = (
    "A leading clause that is long enough,\n"
    "  and a continuation of it.\n"
    "A new sentence here.\n"
)


def test_clause_breaks_are_not_indented_without_the_option() -> None:
    """Existing ``break_clauses`` users see no change: the default is zero."""
    options = {"plugin": {"sembr": {"break_clauses": True}}}
    assert _format(CLAUSES, options) == CLAUSES_BROKEN


@pytest.mark.parametrize(
    "options",
    [
        pytest.param(
            {"plugin": {"sembr": {"break_clauses": True, "continuation_indent": 2}}},
            id="indent-only",
        ),
        pytest.param(
            {
                "plugin": {
                    "sembr": {
                        "break_clauses": True,
                        "preserve_indented_breaks": True,
                    }
                }
            },
            id="and-preserve",
        ),
    ],
)
def test_clause_breaks_are_indented_with_the_option(options: dict) -> None:
    once = _format(CLAUSES, options)
    assert once == CLAUSES_INDENTED
    assert is_md_equal(CLAUSES, once, extensions={"sembr"}, options=options)
    assert _format(once, options) == once


# ---------------------------------------------------------------------------
# Indenting without reading the indent back
#
# The worked example from review. A user wants `break_clauses` styling and does
# not want indentation to preserve anything: if no punctuation justifies a break,
# they expect it gone. That only holds if writing the indent and reading it are
# separate opt-ins — otherwise the formatter's own indent becomes the reason to
# keep a break whose punctuation the author has since deleted.
# ---------------------------------------------------------------------------

_OPENING = "All human beings are born free and equal in dignity and rights.\n"
_CLOSING = "and should act towards one another in a spirit of brotherhood.\n"

#: The author deletes the Oxford comma but leaves the indent the formatter wrote.
COMMA_DELETED = f"{_OPENING}They are endowed with reason and conscience\n  {_CLOSING}"

#: Nothing justifies that break any more, so it goes — indent and all.
COMMA_DELETED_REFLOWED = (
    f"{_OPENING}They are endowed with reason and conscience "
    "and should act towards one another in a spirit of brotherhood.\n"
)


def _clauses(**overrides: object) -> dict:
    return {"plugin": {"sembr": {"break_clauses": True, **overrides}}}


def test_indenting_alone_does_not_keep_a_break_it_could_not_derive() -> None:
    """The formatter's own indent must not outlive the punctuation behind it."""
    options = _clauses(continuation_indent=INDENT)
    once = _format(COMMA_DELETED, options)
    assert once == COMMA_DELETED_REFLOWED
    assert _format(once, options) == once


def test_preserving_keeps_that_break_once_the_punctuation_is_gone() -> None:
    """With the reading half opted into, the indent is the record and it stands."""
    options = _clauses(preserve_indented_breaks=True)
    assert _format(COMMA_DELETED, options) == COMMA_DELETED


def test_restoring_the_comma_restores_the_break_either_way() -> None:
    """The derivable half does not depend on the reading half at all."""
    with_comma = COMMA_DELETED_REFLOWED.replace("conscience and", "conscience, and")
    expected = COMMA_DELETED.replace("conscience\n", "conscience,\n")
    for options in (
        _clauses(continuation_indent=INDENT),
        _clauses(preserve_indented_breaks=True),
    ):
        assert _format(with_comma, options) == expected


def test_the_fixture_is_already_formatted_under_the_option() -> None:
    """The fixture doubles as documentation, so it must not merely survive.

    AST safety and idempotency would pass even if the option silently changed
    the fixture's shape. Asserting it comes back byte-for-byte is what keeps
    the file honest about the behaviour it illustrates.
    """
    source = (FIXTURES_DIR / "continuations.md").read_text(encoding="utf-8")
    assert _format(source, ON) == source
