"""Continuation indentation must never change the render, in any container.

SemBr rule 2 — a break MUST NOT alter the final rendered output — is the whole
premise of the plugin, and kept breaks add *indentation*, which CommonMark
strips from paragraph continuation lines. This module sweeps that claim across
every block container a paragraph can appear in, against content that would
start a new block if it were not indented.

Two mdformat behaviours make this subtle, and both are regression-tested below:

* ``paragraph()`` escapes a line that could open a block (``#``, ``>``, ``-``,
  ``1.``, ``---``), and its checks read the whole line: anchored at the start,
  at the end too for a bare ``1.`` or ``-``, and after stripping whitespace. So
  the marker recording a break cannot sit anywhere on a line of text; it is
  written on a line of its own.
* An HTML block is neutralised by *indenting* the line four spaces instead, so
  re-indenting must never emit less than the line arrived with.
"""

from __future__ import annotations

import itertools
import re

import mdformat
import pytest
from markdown_it import MarkdownIt
from mdformat._util import is_md_equal

from mdformat_sembr import _plugin

_probe = MarkdownIt("commonmark")
#: A parser tagged the way the plugin tags it when breaks are read back; nothing
#: is installed otherwise.
_tagged = MarkdownIt("commonmark")
_tagged.options["mdformat"] = {"plugin": {"sembr": {"preserve_indented_breaks": True}}}
_plugin.update_mdit(_tagged)

FIRST = "A first line of prose here."

#: A first line that does not end a sentence, so the line after it is a
#: continuation SemBr would not re-break to on its own.
CLAUSE_FIRST = "A first line of prose here,"

#: Every block container a paragraph can be found in. ``{0}`` is the first
#: line, ``{1}`` the continuation (already indented by the caller).
CONTAINERS = {
    "top-level-paragraph": "{0}\n{1}\n",
    "bullet-dash": "- {0}\n  {1}\n",
    "bullet-star": "* {0}\n  {1}\n",
    "bullet-plus": "+ {0}\n  {1}\n",
    "ordered-dot": "1. {0}\n   {1}\n",
    "ordered-paren": "1) {0}\n   {1}\n",
    "ordered-wide-marker": "100. {0}\n     {1}\n",
    "nested-bullet-2-deep": "- outer\n  - {0}\n    {1}\n",
    "nested-bullet-3-deep": "- a\n  - b\n    - {0}\n      {1}\n",
    "loose-list-item": "- {0}\n  {1}\n\n- another item here.\n",
    "block-quote": "> {0}\n> {1}\n",
    "nested-block-quote": "> > {0}\n> > {1}\n",
    "block-quote-lazy-line": "> {0}\n{1}\n",
    "list-in-block-quote": "> - {0}\n>   {1}\n",
    "block-quote-in-list": "- > {0}\n  > {1}\n",
    "second-para-in-list-item": "- first para here.\n\n  {0}\n  {1}\n",
    "list-item-after-para": "intro para here.\n\n- {0}\n  {1}\n",
    "emphasis-across-break": "*{0}\n{1}*\n",
    "strong-across-break": "**{0}\n{1}**\n",
    "link-text-across-break": "[{0}\n{1}](http://example.com)\n",
    "after-backslash-hard-break": "{0}\\\n{1}\n",
    "after-two-space-hard-break": "{0}  \n{1}\n",
}

#: Containers whose break is inside a protected span — link text — where none
#: is kept: the paragraph collapses there as the defaults collapse it, so it
#: owes idempotency only where they manage it (bugrasan/mdformat-sembr#9).
UNKEPT = {"link-text-across-break"}

#: Continuation payloads. Everything after "plain prose" would start a new
#: block, or change the enclosing one, if it were not indented.
PAYLOADS = {
    "plain-prose": "a plain continuation line.",
    "bullet-marker": "- looks like a bullet.",
    "ordered-marker": "1. looks like an ordered item.",
    "atx-heading": "# looks like a heading.",
    "block-quote-marker": "> looks like a quote.",
    "fence": "``` looks like a fence.",
    "setext-dashes": "---",
    "setext-equals": "===",
    "thematic-break": "***",
    "html-block": "<div>html</div>",
    "table-row": "| a | b |",
    "bare-year": "2026. a year, not a list.",
    "link": "[a link](http://example.com) here.",
    "code-span": "`code span` here.",
    "escaped-bullet": "\\- an escaped bullet.",
    # A block opener with nothing after it, which mdformat's checks match by
    # their end-of-line anchor rather than the space after the marker.
    "bare-bullet": "-",
    "bare-plus": "+",
    "bare-ordered": "1.",
    "bare-ordered-paren": "1)",
    "bare-heading": "#",
    "escaped-bare-heading": "\\#",
}

AUTHOR_WIDTHS = (1, 2, 3, 4, 5, 8)
INDENT_UNITS = (1, 2, 3, 4)


def _options(unit: int) -> dict:
    return {
        "plugin": {
            "sembr": {
                "preserve_indented_breaks": True,
                "continuation_indent": unit,
            }
        }
    }


#: Indenting without reading the indent back. This is a separate opt-in and it
#: puts indentation on a different set of lines — clause and wrap continuations
#: rather than the ones an author marked — so that output needs sweeping too.
INDENT_ONLY: dict = {"plugin": {"sembr": {"continuation_indent": 2}}}


def _is_paragraph_continuation(source: str) -> bool:
    """True when the second line really is part of a paragraph.

    If it parses as its own block the plugin never sees it, so it says nothing
    about the transform.
    """
    for token in _probe.parse(source):
        if token.type == "inline" and any(
            child.type in ("softbreak", "hardbreak")
            for child in token.children or ()
        ):
            return True
    return False


def _recorded_indent(source: str) -> int:
    """The indentation markdown-it attributes to the break.

    A container can absorb the author's spaces — ``>`` swallows its own — so
    this, not the raw source width, is what the plugin has to work from.
    """
    for token in _tagged.parse(source):
        if token.type == "inline":
            for child in token.children or ():
                if child.type in ("softbreak", "hardbreak"):
                    return child.meta.get("sembr_indent", 0)
    return 0


@pytest.mark.parametrize("container", sorted(CONTAINERS))
@pytest.mark.parametrize("payload", sorted(PAYLOADS))
def test_indentation_never_changes_the_render(container: str, payload: str) -> None:
    template, body = CONTAINERS[container], PAYLOADS[payload]
    checked = 0
    for width, unit in itertools.product(AUTHOR_WIDTHS, INDENT_UNITS):
        source = template.format(FIRST, " " * width + body)
        if not _is_paragraph_continuation(source):
            continue
        options = _options(unit)
        checked += 1
        formatted = mdformat.text(source, extensions={"sembr"}, options=options)
        assert is_md_equal(
            source, formatted, extensions={"sembr"}, options=options
        ), (
            f"render changed: {container}/{payload} "
            f"width={width} unit={unit}\n  in : {source!r}\n  out: {formatted!r}"
        )
        if container in UNKEPT:
            baseline = mdformat.text(source, extensions={"sembr"})
            if mdformat.text(baseline, extensions={"sembr"}) != baseline:
                continue
        assert (
            mdformat.text(formatted, extensions={"sembr"}, options=options)
            == formatted
        ), f"not idempotent: {container}/{payload} width={width} unit={unit}"
    assert checked, f"no case exercised for {container}/{payload}"


@pytest.mark.parametrize("container", sorted(CONTAINERS))
@pytest.mark.parametrize("payload", sorted(PAYLOADS))
def test_indenting_alone_never_changes_the_render(container: str, payload: str) -> None:
    """The same claim for the writing half on its own.

    Render equality is asserted; idempotency deliberately is not, and the reason
    is not this option's to fix. With the reading half off nothing is marked, so
    the paragraph collapses exactly as it does under the plugin's existing
    defaults — which strands an escape mdformat wrote for a line start, so the
    next run drops it as no longer needed. Measured over this matrix, the
    defaults and this option are non-idempotent on the same 714 of 2079
    documents, and ``preserve_indented_breaks`` is the only mode with none. See
    :func:`test_indenting_matches_the_plugin_default_where_nothing_is_marked`
    and https://codeberg.org/bugrasan/mdformat-sembr/issues/9.
    """
    template, body = CONTAINERS[container], PAYLOADS[payload]
    checked = 0
    for width in AUTHOR_WIDTHS:
        source = template.format(FIRST, " " * width + body)
        if not _is_paragraph_continuation(source):
            continue
        checked += 1
        formatted = mdformat.text(source, extensions={"sembr"}, options=INDENT_ONLY)
        assert is_md_equal(
            source, formatted, extensions={"sembr"}, options=INDENT_ONLY
        ), (
            f"render changed: {container}/{payload} width={width}\n"
            f"  in : {source!r}\n  out: {formatted!r}"
        )
    assert checked, f"no case exercised for {container}/{payload}"


def test_indenting_matches_the_plugin_default_where_nothing_is_marked() -> None:
    """Indenting alone cannot move a paragraph that has nothing to indent.

    Asserting this is what keeps a pre-existing behaviour from being
    attributed to the new option: with the reading half off, an indented source
    line is not a continuation, so the paragraph is collapsed exactly as the
    plugin already collapses it, byte for byte. Whatever the escape churn above
    costs, this option neither causes it nor inherits more of it. The reading
    half avoids it for an indented line like this one, because a kept line
    stays a line and keeps needing its escape; an unindented line is collapsed
    as the defaults collapse it, churn and all.
    """
    source = f"{FIRST}\n    - looks like a bullet.\n"
    assert mdformat.text(source, extensions={"sembr"}, options=INDENT_ONLY) == (
        mdformat.text(source, extensions={"sembr"})
    )
    preserved = mdformat.text(source, extensions={"sembr"}, options=_options(2))
    assert (
        mdformat.text(preserved, extensions={"sembr"}, options=_options(2)) == preserved
    )


def test_line_start_escaping_survives_a_kept_break() -> None:
    """mdformat escapes a line that could open a block; a kept break must not hide it.

    The marker recording a break is written on a line of its own, because
    mdformat's checks read the whole line. Anywhere on a line of text it would
    silently suppress the escape, and the line would become a list item,
    heading or quote.
    """
    options = _options(2)
    for body, escaped in [
        ("\\- an escaped bullet.", "\\-"),
        ("\\# an escaped heading.", "\\#"),
        ("\\> an escaped quote.", "\\>"),
    ]:
        source = f"{FIRST}\n  {body}\n"
        formatted = mdformat.text(source, extensions={"sembr"}, options=options)
        assert escaped in formatted, f"escape lost for {body!r}: {formatted!r}"
        assert is_md_equal(source, formatted, extensions={"sembr"}, options=options)


#: Each way of writing a line that is a block opener and nothing else. mdformat
#: matches these by the end-of-line half of ``( |\t|$)`` or a whole-line test,
#: which is exactly what a marker written on the line itself would defeat.
BARE_OPENERS = pytest.mark.parametrize(
    "opener", ["-", "+", "1.", "1)", "#", "\\#", "\\-", "\\+", "---", "==="]
)

#: Where such a line can sit: after a soft break, after a hard break, or first.
OPENER_PLACES = pytest.mark.parametrize(
    "template",
    [
        pytest.param(
            "Hello there world.\n{0}\nNext sentence here is long.\n", id="soft"
        ),
        pytest.param(
            "Hello there world.\\\n{0}\nNext sentence here is long.\n", id="hard"
        ),
        pytest.param("{0}\nfoo bar baz and some more.\n", id="first-line"),
    ],
)

BOTH_HALVES = pytest.mark.parametrize(
    "options",
    [pytest.param(INDENT_ONLY, id="indent"), pytest.param(_options(2), id="preserve")],
)


@BARE_OPENERS
@OPENER_PLACES
@BOTH_HALVES
def test_a_bare_block_opener_keeps_its_escape(
    opener: str, template: str, options: dict
) -> None:
    source = template.format(opener)
    formatted = mdformat.text(source, extensions={"sembr"}, options=options)
    assert is_md_equal(source, formatted, extensions={"sembr"}, options=options), (
        f"{source!r} -> {formatted!r}"
    )
    assert not set(formatted) & set("\x01\x02\x03\x04\x05\x06")
    # Idempotency is owed wherever the plugin's own defaults manage it. Where
    # they do not, collapsing has carried an escaped opener mid-line and the
    # next run drops the backslash it no longer needs — which the defaults do
    # too: https://codeberg.org/bugrasan/mdformat-sembr/issues/9
    baseline = mdformat.text(source, extensions={"sembr"})
    if mdformat.text(baseline, extensions={"sembr"}) == baseline:
        assert (
            mdformat.text(formatted, extensions={"sembr"}, options=options) == formatted
        )


def test_a_non_breaking_space_ending_a_line_is_still_stripped() -> None:
    """mdformat strips every line before checking it, U+00A0 included, so the
    ``#`` left behind is escaped — provided nothing else ends the line."""
    source = "#&nbsp;\nfoo bar baz and more.\n"
    options = _options(2)
    formatted = mdformat.text(source, extensions={"sembr"}, options=options)
    assert is_md_equal(source, formatted, extensions={"sembr"}, options=options)
    assert formatted.startswith("\\#")


@pytest.mark.parametrize("marker", ["-", "+", "*", "#", "1.", "1)"])
@pytest.mark.parametrize("space", ["\u00a0", "\u2003"], ids=["nbsp", "em-space"])
def test_a_unicode_space_after_a_block_marker_stays_on_a_kept_line(
    marker: str, space: str
) -> None:
    """Only a space or a tab after one of these opens a block, so mdformat leaves
    the line as it is. Collapsing that space into an ordinary one would open
    the block on the line the break keeps."""
    source = f"{FIRST}\n  {marker}{space}qux here.\n"
    options = _options(2)
    formatted = mdformat.text(source, extensions={"sembr"}, options=options)
    assert is_md_equal(source, formatted, extensions={"sembr"}, options=options)
    assert f"{space}qux" in formatted
    assert (
        mdformat.text(formatted, extensions={"sembr"}, options=options) == formatted
    )


@pytest.mark.parametrize(
    "line_end", ["\x0b", "\x0c", "\x1c", "\x85", "\u2028", "\u2029"], ids=repr
)
@pytest.mark.parametrize("wrap", ["keep", 20], ids=str)
def test_a_character_that_ends_a_line_is_still_collapsed(
    line_end: str, wrap: object
) -> None:
    """Python splits lines at these, and so does mdformat's quote renderer, so
    one kept like a non-breaking space would end the quote's line early."""
    source = f"> alpha bravo charlie delta\n> echo{line_end}- foxtrot\n"
    options = {**_options(2), "wrap": wrap}
    formatted = mdformat.text(source, extensions={"sembr"}, options=options)
    assert is_md_equal(source, formatted, extensions={"sembr"}, options=options)
    assert (
        mdformat.text(formatted, extensions={"sembr"}, options=options) == formatted
    )


@pytest.mark.parametrize("container", sorted(CONTAINERS))
@pytest.mark.parametrize("wrap", ["keep", 40], ids=str)
def test_a_tilde_fence_line_is_never_kept(container: str, wrap: object) -> None:
    """mdformat escapes no tilde fence, so a line starting with one is never
    kept, nor indented: it is joined to the line above, as by the defaults.

    After a hard break the line stays a line whatever the plugin does, and the
    defaults fail it too, so there only the defaults' result is owed.
    """
    source = CONTAINERS[container].format(FIRST, "    ~~~ qux here.")
    if not _is_paragraph_continuation(source):
        return
    options = {**_options(2), "wrap": wrap}
    baseline = mdformat.text(source, extensions={"sembr"}, options={"wrap": wrap})
    if not is_md_equal(source, baseline, extensions={"sembr"}):
        assert "hard-break" in container
        return
    formatted = mdformat.text(source, extensions={"sembr"}, options=options)
    assert is_md_equal(source, formatted, extensions={"sembr"}, options=options), (
        f"{source!r} -> {formatted!r}"
    )
    assert (
        mdformat.text(formatted, extensions={"sembr"}, options=options) == formatted
    )


#: A break with a line of nothing but whitespace on one side of it, which
#: mdformat strips to an empty line. Encoded whitespace survives the parser as
#: text, so it is the usual way to write one.
EMPTY_LINE_NEIGHBOURS = [
    pytest.param("Foo bar\n&#32;\n", id="empty-last-line"),
    pytest.param("Foo bar \n&#9;\n", id="empty-last-line-tab"),
    pytest.param("&#9;\n  Foo bar\n", id="empty-first-line"),
    pytest.param("Foo bar.\n\u00a0\n  more text here\n", id="nbsp-line"),
    pytest.param(";\n\u00a0\n|\n", id="nbsp-line-in-table-fallback"),
    pytest.param("|\n&nbsp;\nh\n", id="entity-line-in-table-fallback"),
    pytest.param("*\\\n\u00a0\n\t<div>\n", id="nbsp-line-after-hard-break"),
    pytest.param(".  \n&nbsp;\n|\n", id="entity-line-after-hard-break"),
]


@pytest.mark.parametrize("source", EMPTY_LINE_NEIGHBOURS)
@pytest.mark.parametrize("wrap", ["keep", "no", 1, 10, 40], ids=str)
def test_a_break_beside_an_empty_line_is_not_marked(source: str, wrap: object) -> None:
    """There is nothing to keep, and a marker there can land where it cannot be
    folded away: mdformat writes a paragraph's outermost whitespace as an
    entity, newline included. Held to whatever the defaults manage."""
    options = {**_options(2), "wrap": wrap}
    formatted = mdformat.text(source, extensions={"sembr"}, options=options)
    assert not set(formatted) & set("\x01\x02\x03\x04\x05\x06")
    defaults = {"wrap": wrap}
    baseline = mdformat.text(source, extensions={"sembr"}, options=defaults)
    if is_md_equal(source, baseline, extensions={"sembr"}):
        assert is_md_equal(source, formatted, extensions={"sembr"}, options=options)
    if mdformat.text(baseline, extensions={"sembr"}, options=defaults) == baseline:
        assert (
            mdformat.text(formatted, extensions={"sembr"}, options=options) == formatted
        )


@pytest.mark.parametrize("wrap", ["no", 40], ids=str)
def test_no_break_is_kept_in_an_image_description(wrap: object) -> None:
    """It is plain text, without the escapes a paragraph's lines get, so a kept
    break could leave a backslash ending a line, and so a hard break. (Without
    word wrapping mdformat keeps that line itself, and fails the same way.)"""
    source = "Text ![foo \\ \n  bar](u) more.\n"
    options = {**_options(2), "wrap": wrap}
    formatted = mdformat.text(source, extensions={"sembr"}, options=options)
    assert is_md_equal(source, formatted, extensions={"sembr"}, options=options)
    defaults = mdformat.text(source, extensions={"sembr"}, options={"wrap": wrap})
    assert formatted == defaults


@pytest.mark.parametrize(
    "source",
    [
        pytest.param("\u00a0</a>\n|\n", id="lone-tag"),
        pytest.param("- item\n\n  \u00a0</a>\u00a0\n  |\n  _ x\n", id="in-a-list"),
    ],
)
@pytest.mark.parametrize("wrap", ["no", 40], ids=str)
def test_a_paragraph_taken_for_a_table_keeps_no_breaks(
    source: str, wrap: object
) -> None:
    """The table fallback returns such a paragraph as mdformat gives it, so the
    breaks it kept would skip every guard a reflow applies, where word wrapping
    would have joined their lines. Here a tag left alone on the first line would
    open an HTML block."""
    options = {**_options(2), "wrap": wrap}
    formatted = mdformat.text(source, extensions={"sembr"}, options=options)
    assert is_md_equal(source, formatted, extensions={"sembr"}, options=options)
    defaults = mdformat.text(source, extensions={"sembr"}, options={"wrap": wrap})
    assert formatted == defaults


def test_html_block_guard_is_never_narrowed() -> None:
    """mdformat neutralises an HTML block by indenting four spaces, not escaping.

    Re-indenting to a narrower width would hand the line back its block
    meaning, so the incoming indent is a floor.
    """
    source = f"{FIRST}\n    <div>html</div>\n"
    assert _is_paragraph_continuation(source)
    for unit in (1, 2, 3, 4, 8):
        options = _options(unit)
        formatted = mdformat.text(source, extensions={"sembr"}, options=options)
        continuation = formatted.split("\n")[1]
        assert len(continuation) - len(continuation.lstrip(" ")) >= 4, formatted
        assert is_md_equal(source, formatted, extensions={"sembr"}, options=options)


@pytest.mark.parametrize("break_clauses", [False, True], ids=["plain", "clauses"])
def test_the_html_block_guard_stays_on_the_line_it_guards(break_clauses: bool) -> None:
    """The guard belongs to the line mdformat guarded, not to everything after it.

    Reflow can make several lines out of a kept line that started with HTML.
    Copied onto the later ones, the four spaces would be read back on the next
    run as a break the author kept, and the document would keep changing.
    """
    source = (
        f"{FIRST}\n    <!-- c --> More text here."
        " Another sentence here, with a clause.\n"
    )
    options = {
        "plugin": {
            "sembr": {"preserve_indented_breaks": True, "break_clauses": break_clauses}
        }
    }
    once = mdformat.text(source, extensions={"sembr"}, options=options)
    assert is_md_equal(source, once, extensions={"sembr"}, options=options)
    assert mdformat.text(once, extensions={"sembr"}, options=options) == once
    assert once.split("\n")[1].startswith("    <!--")
    assert once.split("\n")[2].startswith("Another")


@pytest.mark.parametrize("container", sorted(CONTAINERS))
def test_indent_is_applied_in_every_container(container: str) -> None:
    """A recorded indent becomes exactly one continuation indent, everywhere a
    break is kept — and inside a link's text, where none is, none at all.

    The continuation here is deliberately one SemBr would not break to on its
    own — the line before it ends in a comma, not a sentence — so the indent is
    load-bearing and must be re-emitted rather than dropped as redundant.
    """
    template = CONTAINERS[container]
    marker = "zzcontinuationzz"
    checked = 0
    for width, unit in itertools.product(AUTHOR_WIDTHS, INDENT_UNITS):
        source = template.format(
            CLAUSE_FIRST, " " * width + f"{marker} continues here."
        )
        if not _is_paragraph_continuation(source):
            continue
        options = _options(unit)
        # The container's own continuation indent, measured from two sentences
        # SemBr splits by itself with nothing authored.
        baseline = mdformat.text(
            template.format("Alpha sentence here.", "Beta sentence here."),
            extensions={"sembr"},
            options=options,
        )
        formatted = mdformat.text(source, extensions={"sembr"}, options=options)
        base_indent = _indent_of(baseline, "Beta sentence")
        applied = _indent_of(formatted, marker) - base_indent
        kept = _recorded_indent(source) and container not in UNKEPT
        expected = unit if kept else 0
        checked += 1
        assert applied == expected, (
            f"{container} width={width} unit={unit}: applied {applied}, "
            f"expected {expected}\n  {formatted!r}"
        )
    assert checked, f"no case exercised for {container}"


def _indent_of(document: str, needle: str) -> int:
    """Leading spaces of the line holding ``needle``, ignoring quote markers."""
    for line in document.split("\n"):
        if needle in line:
            body = re.sub(r"^(?:[ \t]*>)*", "", line)
            return len(body) - len(body.lstrip(" "))
    raise AssertionError(f"{needle!r} not found in {document!r}")


@pytest.mark.parametrize(
    ("name", "source"),
    [
        pytest.param(
            "setext-heading", "A heading here\n  second line\n===\n", id="heading"
        ),
        pytest.param(
            "table-fallback",
            "Intro sentence here.\n  | not really a table |\n",
            id="table-fallback",
        ),
        pytest.param(
            "code-span-across-break",
            "A sentence with `code\n  span` inside it here.\n",
            id="code-span",
        ),
        pytest.param(
            "inline-html-across-break",
            "A sentence with <span\n  class='x'>markup</span> here.\n",
            id="inline-html",
        ),
        pytest.param(
            "tag-alone-on-first-line",
            "<span\nclass=x>\n  more text here.\n",
            id="lone-tag-first-line",
        ),
        pytest.param(
            "tag-alone-then-an-emptied-line",
            "<span\nclass=x>\n  &nbsp;\nmore text here.\n",
            id="lone-tag-then-entity-line",
        ),
        pytest.param(
            "tag-alone-then-an-nbsp-line",
            "<a\nhref=x>\n  \u00a0\n  more text here.\n",
            id="lone-tag-then-nbsp-line",
        ),
        pytest.param(
            "hard-break-in-alt-text",
            "Text ![foo \\\nbar](u) more.\n",
            id="image-alt",
        ),

    ],
)
def test_contexts_that_drop_the_indent_are_still_safe(name: str, source: str) -> None:
    """Some contexts cannot carry a kept break. They must still round-trip cleanly.

    A heading folds to one line; the no-table-plugin fallback leaves the block
    verbatim; a newline inside a code span or inline HTML is not a soft break
    at all, so there is no break to keep. A break straight after a tag the
    source split across a paragraph's first line is not kept, because the tag
    would be left alone on that line, where it opens an HTML block — and a line
    mdformat empties in between changes nothing. An image's alt text is plain
    text, so mdformat writes a hard break there as nothing.
    """
    options = _options(2)
    formatted = mdformat.text(source, extensions={"sembr"}, options=options)
    assert is_md_equal(source, formatted, extensions={"sembr"}, options=options)
    assert (
        mdformat.text(formatted, extensions={"sembr"}, options=options) == formatted
    )
    assert not set(formatted) & set("\x01\x02\x03\x04\x05\x06")
