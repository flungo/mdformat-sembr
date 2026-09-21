"""Continuation indentation must never change the render, in any container.

SemBr rule 2 — a break MUST NOT alter the final rendered output — is the whole
premise of the plugin, and pinned breaks add *indentation*, which CommonMark
strips from paragraph continuation lines. This module sweeps that claim across
every block container a paragraph can appear in, against content that would
start a new block if it were not indented.

Two mdformat behaviours make this subtle, and both are regression-tested below:

* ``paragraph()`` escapes a line that could open a block (``#``, ``>``, ``-``,
  ``1.``, ``---``) — but every check is anchored at column zero, so the
  continuation marker must not sit at the start of a line.
* An HTML block is neutralised by *indenting* the line four spaces instead, so
  re-indenting must never emit less than the line arrived with.
"""

from __future__ import annotations

import itertools

import mdformat
import pytest
from markdown_it import MarkdownIt
from mdformat._util import is_md_equal

from mdformat_sembr import _plugin

_probe = MarkdownIt("commonmark")
_tagged = MarkdownIt("commonmark")
_plugin.update_mdit(_tagged)

FIRST = "A first line of prose here."

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
}

AUTHOR_WIDTHS = (1, 2, 3, 4, 5, 8)
INDENT_UNITS = (1, 2, 3, 4)


def _options(unit: int) -> dict:
    return {
        "plugin": {
            "sembr": {"preserve_indented_breaks": True, "continuation_indent": unit}
        }
    }


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
        assert (
            mdformat.text(formatted, extensions={"sembr"}, options=options)
            == formatted
        ), f"not idempotent: {container}/{payload} width={width} unit={unit}"
    assert checked, f"no case exercised for {container}/{payload}"


def test_line_start_escaping_survives_a_pinned_break() -> None:
    """mdformat escapes a line that could open a block; a pin must not hide it.

    The marker is written at the end of the line a break closes precisely
    because mdformat's checks are anchored at column zero. With the marker at
    the start instead, the escape is silently skipped and the line becomes a
    list item, heading or quote.
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


@pytest.mark.parametrize("container", sorted(CONTAINERS))
def test_indent_is_applied_in_every_container(container: str) -> None:
    """Whatever indentation the parser recorded is what gets re-emitted."""
    template = CONTAINERS[container]
    marker = "Zzcontinuationzz"
    checked = 0
    for width, unit in itertools.product(AUTHOR_WIDTHS, INDENT_UNITS):
        source = template.format(FIRST, " " * width + f"{marker} continues here.")
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
        recorded = _recorded_indent(source)
        expected = unit * max(1, recorded // unit) if recorded else 0
        checked += 1
        assert applied == expected, (
            f"{container} width={width} unit={unit}: applied {applied}, "
            f"expected {expected}\n  {formatted!r}"
        )
    assert checked, f"no case exercised for {container}"


def _indent_of(document: str, needle: str) -> int:
    """Leading spaces of the line holding ``needle``, ignoring quote markers."""
    import re

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
    ],
)
def test_contexts_that_drop_the_indent_are_still_safe(name: str, source: str) -> None:
    """Some contexts cannot carry a pin. They must still round-trip cleanly.

    A heading folds to one line; the no-table-plugin fallback leaves the block
    verbatim; a newline inside a code span or inline HTML is not a soft break
    at all, so there is no break to pin.
    """
    options = _options(2)
    formatted = mdformat.text(source, extensions={"sembr"}, options=options)
    assert is_md_equal(source, formatted, extensions={"sembr"}, options=options)
    assert (
        mdformat.text(formatted, extensions={"sembr"}, options=options) == formatted
    )
    assert "\x01" not in formatted
