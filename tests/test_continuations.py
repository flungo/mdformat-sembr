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
from markdown_it import MarkdownIt
from mdformat._util import is_md_equal

from mdformat_sembr._sembr import CONTINUATION_MARK, insert_breaks
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

#: The width used throughout, and the one ``preserve_indented_breaks`` defaults to.
INDENT = 2

#: Every character the plugin uses internally to mark a break.
PROTOCOL_CHARS = frozenset("\x01\x02\x03\x04\x05\x06")

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


@pytest.mark.parametrize("width", [0, -2], ids=["zero", "negative"])
def test_a_zero_width_turns_preserving_off_rather_than_breaking_the_round_trip(
    width: int,
) -> None:
    """A negative width indents nothing either, so it is the same contradiction."""
    options = _opts(continuation_indent=width)
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
def test_enabling_leaves_the_plugins_own_breaks_alone(options: dict) -> None:
    """Enabling either half changes nothing the defaults write for the fixtures.

    True of either half, and for different reasons. Reading finds nothing to
    keep, because this plugin's own output is flush left. Writing finds nothing
    to indent among the plugin's own breaks, because without clause breaking
    those fall only where a sentence starts — the one place a continuation
    indent never goes.

    Two things writing does indent are absent from these fixtures, and that is
    what it is for: the clause continuations ``break_clauses`` makes, and a
    line after a hard break that continues the sentence above it, as an address
    written one line to a row does.
    """
    for name, source in fixture_sources():
        if name in INDENTED_FIXTURES:
            continue
        baseline = mdformat.text(source, extensions={"sembr"})
        assert _format(source, options) == baseline, f"fixture {name!r} changed"
    # A short sentence before a hard break does not make the next one look like
    # a continuation: min_chars says nothing about where a sentence starts.
    formatted = "Roses bloom.\\\nSugar is sweet.\n"
    assert _format(formatted, options) == formatted


def test_fixtures_stay_ast_safe_and_idempotent_with_the_option_on() -> None:
    for name, source in fixture_sources():
        once = _format(source, ON)
        assert is_md_equal(
            source, once, extensions={"sembr"}, options=ON
        ), f"AST changed for fixture {name!r}"
        assert _format(once, ON) == once, f"not idempotent for fixture {name!r}"


def test_accidental_hard_wrapping_is_still_reflowed() -> None:
    """A legacy document wrapped at a column width has no indentation to keep."""
    source = (
        "This document was hard wrapped at eighty columns by an older tool and\n"
        "therefore breaks in arbitrary places here.\n"
    )
    assert _format(source, ON) == (
        "This document was hard wrapped at eighty columns by an older tool "
        "and therefore breaks in arbitrary places here.\n"
    )


def test_kept_group_is_still_reflowed_internally() -> None:
    """A kept break stops a collapse; it does not stop SemBr breaking."""
    source = (
        "Intro clause here:\n"
        "  a kept continuation. With a second sentence inside it.\n"
        "After the group. And more text after that too.\n"
    )
    assert _format(source, ON) == (
        "Intro clause here:\n"
        "  a kept continuation.\n"
        # A new sentence is not a continuation, so it returns to the margin
        # even though it came out of a kept line.
        "With a second sentence inside it.\n"
        "After the group.\n"
        "And more text after that too.\n"
    )


@pytest.mark.parametrize(
    "options",
    [pytest.param(INDENT_ONLY, id="indent"), pytest.param(ON, id="preserve")],
)
def test_a_sentence_the_reflow_broke_before_stays_at_the_margin(options: dict) -> None:
    """The reflow knows which of its breaks begin sentences, and is not asked
    again: two lines taken out of their paragraph mask the code spans in them
    under different placeholders, one character shorter here, and min_chars
    would then fall one short of the break."""
    spans = " ".join(f"`c{i}`" for i in range(10))
    source = f"Spans {spans} here.\n`a` bcdef.\nNext sentence follows here.\n"
    assert mdformat.text(source, extensions={"sembr"}) == source
    assert _format(source, options) == source


def test_kept_break_ignores_min_chars() -> None:
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


def test_sentences_return_to_the_margin_however_deeply_indented() -> None:
    """A sentence SemBr breaks before is never a continuation, whatever the
    author indented. One too short for ``min_chars`` is not such a sentence."""
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
            "- An item sentence here.\n    a kept continuation of the item.\n",
            id="list-item",
        ),
        pytest.param(
            "> A quoted sentence here.\n>   a kept continuation of it.\n",
            id="block-quote",
        ),
        pytest.param(
            "1. An item sentence here.\n     a kept continuation of it.\n",
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
def test_kept_breaks_survive_inside_containers(source: str) -> None:
    once = _format(source, ON)
    assert once == source
    assert is_md_equal(source, once, extensions={"sembr"}, options=ON)


@pytest.mark.parametrize("wrap", ["keep", "no", 30, 60], ids=str)
def test_kept_breaks_outrank_word_wrapping(wrap: object) -> None:
    """SemBr owns the line structure, so ``--wrap N`` must not move a kept break."""
    source = (
        "Intro sentence at the top here.\n"
        "  a kept continuation that is quite a lot longer than the wrap width.\n"
        "Back at the top level again.\n"
    )
    options = {**ON, "wrap": wrap}
    once = _format(source, options)
    assert once == source
    assert is_md_equal(source, once, extensions={"sembr"}, options=options)


@pytest.mark.parametrize("wrap", ["keep", "no", 20], ids=str)
def test_marker_never_reaches_the_output(wrap: object) -> None:
    """The sentinels are internal; a stray one would corrupt the document."""
    source = REFERENCE + "\nA hard break here,\\\n  and a soft one\nafter it.\n"
    out = _format(source, {**ON, "wrap": wrap})
    assert not PROTOCOL_CHARS & set(out)


@pytest.mark.parametrize(
    "source",
    [
        pytest.param("A sentence with a \x01 literal control in it.\n", id="mid-line"),
        pytest.param("Foo bar baz\x01\nqux quux. Next one here.\n", id="before-soft"),
        pytest.param("Foo bar baz\x02\nqux quux. Next one here.\n", id="before-soft-2"),
        pytest.param("Foo bar baz qux.\x01\n", id="paragraph-end"),
        pytest.param("Foo bar baz\x02\\\nqux quux.\n", id="before-hard"),
        pytest.param("Foo bar baz\n\x03\nqux quux.\n", id="soft-mark-line"),
        pytest.param("Foo bar\\\n\x03\nbaz qux\n", id="soft-mark-after-hard"),
        pytest.param("Foo bar\\\n\x05\nbaz qux\n", id="hard-mark-line"),
        pytest.param("Foo bar\n  \x04\nbaz qux\n", id="indented-mark-line"),
        pytest.param("aaaaa \x03 bbbbb \x06 ccccc\n", id="mark-word"),
        pytest.param("aaaaa\x01 bbbbb\x02 ccccc\n", id="mark-before-wrap"),
        pytest.param("Some <span title='a\x02\nb'>x</span> here.\n", id="in-html"),
        pytest.param('Some [a](u "x\x01\ny") here.\n', id="in-link-title"),
    ],
)
@pytest.mark.parametrize("wrap", ["keep", 5], ids=str)
@pytest.mark.parametrize(
    "options",
    [
        pytest.param({}, id="off"),
        pytest.param(INDENT_ONLY, id="indent"),
        pytest.param(ON, id="preserve"),
    ],
)
def test_a_control_character_in_the_source_is_left_alone(
    source: str, wrap: object, options: dict
) -> None:
    """The markers are control characters, so a literal one must survive.

    A paragraph already holding any of them is not marked at all, so nothing
    in it is read as a marker: not one before a newline that word wrapping or
    an inline construct put there, nor one on a line of its own, which is where
    a marker line goes.
    """
    options = {**options, "wrap": wrap}
    out = _format(source, options)
    assert is_md_equal(source, out, extensions={"sembr"}, options=options)
    assert _format(out, options) == out
    assert sorted(c for c in out if c in PROTOCOL_CHARS) == sorted(
        c for c in source if c in PROTOCOL_CHARS
    )


@pytest.mark.parametrize(
    "source",
    [
        pytest.param(
            'Some [link text\n  continues here](/url "A title here. 1. With more")\n',
            id="link-title",
        ),
        pytest.param(
            "[Foo bar baz qux. Quux corge grault.\n"
            "  Garply waldo](https://example.com) fred.\n",
            id="link-text",
        ),
        pytest.param(
            "Alpha bravo \\` charlie delta. Echo foxtrot golf.\n"
            "  Hotel india \\` juliet.\n",
            id="escaped-backticks",
        ),
    ],
)
def test_no_break_is_kept_inside_a_protected_span(source: str) -> None:
    """A span the plugin never breaks inside is found in the paragraph as a
    whole, as without preservation. Kept, a break inside one would split it
    between two groups that each reflow alone, neither seeing the span whole —
    so a link's title, say, would be broken at its sentences."""
    once = _format(source, ON)
    assert once == mdformat.text(source, extensions={"sembr"})
    assert is_md_equal(source, once, extensions={"sembr"}, options=ON)
    assert _format(once, ON) == once


@pytest.mark.parametrize("start", ["\\:", "\\~", "\\%", "~~~"])
@pytest.mark.parametrize(
    "options",
    [pytest.param(INDENT_ONLY, id="indent"), pytest.param(ON, id="preserve")],
)
def test_a_line_another_plugin_escapes_is_never_indented(
    start: str, options: dict
) -> None:
    """mdformat-deflist escapes ``:`` and ``~``, and mdformat-myst ``%``, only
    in a line's first column, though their parsers read the marker however far
    it is indented — and their escaping may run after this plugin's. Such a
    line therefore stays where they look: never indented, and so never kept.
    mdformat itself escapes no tilde fence, so the same holds for one."""
    kept = f"Foo bar baz qux quux\n  {start} not a block.\n"
    assert _format(kept, options) == mdformat.text(kept, extensions={"sembr"})
    clause = {
        "plugin": {"sembr": {**options["plugin"]["sembr"], "break_clauses": True}}
    }
    reflowed = f"Foo bar baz qux quux, {start} and the rest of it here.\n"
    defaults = {"plugin": {"sembr": {"break_clauses": True}}}
    assert _format(reflowed, clause) == _format(reflowed, defaults)


def test_headings_are_not_indented() -> None:
    """A setext heading folds to one line; a continuation marker there would leak."""
    source = "A heading here\nsecond line\n===\n"
    assert _format(source, ON) == "# A heading here second line\n"


def test_tables_are_left_alone() -> None:
    source = "| Col A | Col B |\n| ----- | ----- |\n| one | two |\n"
    once = _format(source, ON)
    assert not PROTOCOL_CHARS & set(once)
    assert is_md_equal(source, once, extensions={"sembr"}, options=ON)


@pytest.mark.parametrize(
    "source",
    [
        pytest.param(
            "Separate the alternatives with a pipe, | as in a shell pipeline.\n",
            id="after-a-clause",
        ),
        pytest.param(
            "| Not a table, just a line of prose that is long enough.\n",
            id="first-line",
        ),
        pytest.param(
            "Separate the alternatives\n  with a pipe between them, | as in a shell.\n",
            id="beside-a-kept-break",
        ),
    ],
)
@pytest.mark.parametrize(
    "options",
    [
        pytest.param({"continuation_indent": INDENT}, id="indent"),
        pytest.param({"preserve_indented_breaks": True}, id="preserve"),
    ],
)
def test_a_paragraph_taken_for_a_table_is_written_flush_left(
    source: str, options: dict
) -> None:
    """A line starting with a pipe makes the next run leave the paragraph as
    mdformat gives it, which is without indentation. Indented now, it would
    change back then."""
    options = {"plugin": {"sembr": {"break_clauses": True, **options}}}
    once = _format(source, options)
    assert any(line.startswith("|") for line in once.split("\n"))
    assert not any(line.startswith(" ") for line in once.split("\n"))
    assert _format(once, options) == once
    assert is_md_equal(source, once, extensions={"sembr"}, options=options)


@pytest.mark.parametrize(
    "source",
    [
        pytest.param(
            "An intro with no clause punctuation at all\n"
            "  a kept continuation of it.\n",
            id="no-preceding-punctuation",
        ),
        pytest.param(
            "An intro ending in a comma,\n  a kept continuation of it.\n",
            id="preceding-punctuation",
        ),
    ],
)
def test_keeping_does_not_depend_on_punctuation(source: str) -> None:
    """Indentation is the signal; the punctuation before it is irrelevant."""
    assert _format(source, ON) == source


@pytest.mark.parametrize(
    "source",
    [
        pytest.param(
            "- level one, with a clause:\n  - level two, and another:\n"
            "    - level three, introducing:\n        a kept continuation.\n",
            id="third-level-list-item",
        ),
        pytest.param(
            "> - A list inside a block quote:\n>     a kept continuation.\n",
            id="list-in-block-quote",
        ),
        pytest.param(
            "- > A block quote inside a list item:\n  >   a kept continuation.\n",
            id="block-quote-in-list",
        ),
        pytest.param(
            "> > A doubly nested quote:\n> >   a kept continuation.\n",
            id="nested-block-quote",
        ),
    ],
)
def test_kept_breaks_survive_at_depth(source: str) -> None:
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
# A user wants `break_clauses` styling and does not want indentation to
# preserve anything: if no punctuation justifies a break, they expect it gone.
# That only holds if writing the indent and reading it are separate opt-ins —
# otherwise the formatter's own indent becomes the reason to keep a break whose
# punctuation the author has since deleted.
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


# The plugin leaves the break before an empty line unmarked, so it never hands
# insert_breaks a marked line with nothing on it. Another caller can, and these
# hold for them.


def test_an_empty_marked_line_does_not_count_as_a_line() -> None:
    """Between a lone tag and the text after it, it would keep the tag alone on
    the paragraph's first line, where it opens an HTML block."""
    text = f"<a href=x>{CONTINUATION_MARK}\n{CONTINUATION_MARK}\nmore text here"
    assert insert_breaks(text, marked=True, continuation_indent=INDENT) == (
        "<a href=x> more text here"
    )


def test_an_empty_marked_line_leaves_no_leading_space() -> None:
    """Opening a group, it would leave a space before the line after it, read
    back on the next run as an indent."""
    text = f"Hello there, wide world.{CONTINUATION_MARK}\n\nFoo bar baz."
    assert insert_breaks(text, marked=True) == "Hello there, wide world.\nFoo bar baz."


def test_a_marked_line_before_a_hard_break_loses_its_marker() -> None:
    """The line it opens holds nothing but the hard break, so the marker
    describes no line, and nothing else would take it off."""
    text = f"First line here{CONTINUATION_MARK}\n\\\nNext line here"
    assert insert_breaks(text, marked=True) == "First line here\n\\\nNext line here"


def test_a_line_holding_only_a_non_breaking_space_is_dropped_cleanly() -> None:
    """mdformat strips such a line to nothing; it must not become a leading
    space, which would read as the start of an HTML-block guard."""
    source = "Next one!\n  Hello world.\n&nbsp;\nThis is a sentence.\n"
    options = _opts(min_chars=5)
    once = _format(source, options)
    assert is_md_equal(source, once, extensions={"sembr"}, options=options)
    assert _format(once, options) == once


# ---------------------------------------------------------------------------
# The parser hooks
# ---------------------------------------------------------------------------


def _parser(sembr: dict) -> MarkdownIt:
    mdit = MarkdownIt("commonmark")
    mdit.options["mdformat"] = {"plugin": {"sembr": sembr}}
    return mdit


@pytest.mark.parametrize(
    "sembr",
    [pytest.param({}, id="off"), pytest.param({"continuation_indent": 2}, id="indent")],
)
def test_without_preserving_the_parser_is_left_alone(sembr: dict) -> None:
    """Indenting the output needs no record of the source, so nothing is hooked."""
    from mdformat_sembr import _plugin

    mdit = _parser(sembr)
    before = [(rule.name, rule.fn) for rule in mdit.inline.ruler.__rules__]
    _plugin.update_mdit(mdit)
    assert [(rule.name, rule.fn) for rule in mdit.inline.ruler.__rules__] == before


def test_preserving_wraps_another_plugins_rule_rather_than_replacing_it() -> None:
    """A plugin loaded earlier may have replaced ``newline``; it keeps working,
    and in every chain it was registered for."""
    from mdformat_sembr import _plugin

    mdit = _parser({"preserve_indented_breaks": True})
    ruler = mdit.inline.ruler
    original = ruler.__rules__[ruler.__find__("newline")].fn
    calls: list[int] = []

    def custom_newline(state, silent):  # type: ignore[no-untyped-def]
        calls.append(state.pos)
        return original(state, silent)

    ruler.at("newline", custom_newline, {"alt": ["custom-chain"]})
    _plugin.update_mdit(mdit)
    assert ruler.__rules__[ruler.__find__("newline")].alt == ["custom-chain"]

    tokens = mdit.parse("A first line here,\n  and the continuation.\n")
    breaks = [t for t in tokens[1].children or () if t.type == "softbreak"]
    assert calls, "the other plugin's rule was replaced, not wrapped"
    assert breaks and breaks[0].meta.get("sembr_indent") == 2


def test_the_fixture_is_already_formatted_under_the_option() -> None:
    """The fixture doubles as documentation, so it must not merely survive.

    AST safety and idempotency would pass even if the option silently changed
    the fixture's shape. Asserting it comes back byte-for-byte is what keeps
    the file honest about the behaviour it illustrates.
    """
    source = (FIXTURES_DIR / "continuations.md").read_text(encoding="utf-8")
    assert _format(source, ON) == source
