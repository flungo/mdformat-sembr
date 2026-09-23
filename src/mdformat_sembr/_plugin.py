"""mdformat parser-extension interface for the SemBr plugin.

This module *is* the plugin interface object referenced by the entry point
``mdformat_sembr:_plugin``. It exposes the members required by
``mdformat.plugins.ParserExtensionInterface`` at module level.

We override no renderer; all work happens in postprocessors, chiefly the one on
the ``paragraph`` node type. At that point inline formatting is already resolved
into the rendered string, so we operate on final text and protect a few inline
constructs by regex.

The parser is touched only to preserve information it would otherwise discard:
two inline rules are wrapped so that line breaks remember how far the line they
open was indented. That is metadata, invisible to rendering, and it is only
acted on when ``preserve_indented_breaks`` is set.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from markdown_it.rules_inline import escape as _default_escape_rule
from markdown_it.rules_inline import newline as _default_newline_rule

from mdformat_sembr._sembr import (
    DEFAULT_ABBREVIATIONS,
    DEFAULT_CLAUSE_CHARS,
    DEFAULT_CONTINUATION_INDENT,
    DEFAULT_MIN_CHARS,
    continuation_mark,
    insert_breaks,
    strip_continuation_marks,
)

if TYPE_CHECKING:
    from markdown_it import MarkdownIt
    from markdown_it.rules_inline import StateInline
    from mdformat.renderer import RenderContext, RenderTreeNode

#: SemBr soft breaks never alter the rendered output, so the AST is unchanged.
#: This lets mdformat's built-in ``is_md_equal`` validator gate correctness.
CHANGES_AST = False


#: Inline token types whose newline opens a new source line.
_BREAK_TOKENS = ("softbreak", "hardbreak")


def _indent_after_break(source: str, start: int, end: int) -> int:
    """Return the indentation of the line opened by a newline in ``source``.

    ``start``/``end`` bound the span an inline rule just consumed. If it did not
    consume a newline the rule was not a line break and the answer is zero.
    """
    newline = source.find("\n", start, end)
    if newline == -1:
        return 0
    cursor = newline + 1
    while cursor < len(source) and source[cursor] in " \t":
        cursor += 1
    return cursor - (newline + 1)


def _track_indent(rule: Any) -> Any:
    """Wrap an inline ``rule`` so breaks it emits carry their line's indent.

    markdown-it discards that indentation while tokenizing — it eats the spaces
    after a newline, and mdformat's ``text`` renderer collapses runs of spaces —
    so by the time a postprocessor sees a paragraph, the author's continuation
    marking is gone. Recording it here, as token metadata, is the last point at
    which it is still available. Metadata is invisible to the HTML renderer, so
    this does not disturb ``is_md_equal`` validation.
    """

    def tracked(state: "StateInline", silent: bool) -> bool:
        start = state.pos
        first_new_token = len(state.tokens)
        handled = rule(state, silent)
        if not handled or silent:
            return handled

        indent = _indent_after_break(state.src, start, state.pos)
        if indent:
            for token in state.tokens[first_new_token:]:
                if token.type in _BREAK_TOKENS:
                    token.meta = {**token.meta, "sembr_indent": indent}
        return handled

    return tracked


def update_mdit(mdit: "MarkdownIt") -> None:
    """Tag line breaks with the indentation of the line they open.

    Both rules matter: ``newline`` produces soft breaks and the two-space hard
    break, while a backslash hard break comes out of ``escape``.
    """
    mdit.inline.ruler.at("newline", _track_indent(_default_newline_rule))
    mdit.inline.ruler.at("escape", _track_indent(_default_escape_rule))


def _plugin_options(context: "RenderContext") -> Mapping[str, Any]:
    """Return the merged ``[plugin.sembr]`` / CLI options mapping."""
    mdformat_opts = context.options.get("mdformat", {})
    plugin_opts = mdformat_opts.get("plugin", {})
    return plugin_opts.get("sembr", {}) or {}


def _in_paragraph(node: "RenderTreeNode") -> bool:
    """Return True if ``node`` renders inside a paragraph rather than a heading."""
    parent = node.parent
    while parent is not None:
        if parent.type in ("paragraph", "heading"):
            return parent.type == "paragraph"
        parent = parent.parent
    return False


def _postprocess_break(
    text: str,
    node: "RenderTreeNode",
    context: "RenderContext",
) -> str:
    """Mark a rendered line break with the indentation level it was written at.

    The marker is consumed again by :func:`_postprocess_paragraph`, so it never
    reaches the output. It also never reaches ``is_md_equal``, which renders
    through markdown-it's HTML renderer rather than mdformat's.

    Every break is marked, not only the indented ones: that is what lets the
    paragraph postprocessor tell a break the author wrote from a newline
    mdformat's own word wrapping introduced.

    The marker precedes the break rather than starting the line it opens — see
    :data:`CONTINUATION_MARK` for why that distinction matters.
    """
    opts = _plugin_options(context)
    if not bool(opts.get("preserve_indented_breaks", False)):
        return text
    if not _in_paragraph(node):
        return text

    # Any indentation at all pins the break, whatever its width: the marker
    # records that the author marked a continuation, not how deeply. Nesting
    # depth is deliberately not carried — a continuation that needs a second
    # level is, in practice, a new sentence, and a new sentence starts at
    # column zero.
    mark = continuation_mark(1 if node.meta.get("sembr_indent", 0) else 0)
    # The marker goes immediately before the break — ahead of a hard break's
    # own backslash, and never at the start of the line the break opens; see
    # CONTINUATION_MARK for why that placement is load-bearing. Keeping it
    # ahead of the backslash also leaves the hard-break split untouched.
    #
    # Under ``--wrap N`` mdformat renders a soft break as a wrap marker rather
    # than a newline; a pinned break outranks word wrapping, so restore it.
    return mark + (text if text.endswith("\n") else "\n")


def _postprocess_paragraph(
    text: str,
    node: "RenderTreeNode",
    context: "RenderContext",
) -> str:
    """Insert SemBr soft breaks into an already-rendered paragraph string."""
    # When no GFM table plugin is active, markdown-it parses tables as
    # paragraphs. Detect that case (multiple lines where at least one starts
    # with '|') and return the text unchanged so the table structure is
    # preserved. With a table plugin the node type is 'table', not 'paragraph',
    # so this guard is never reached for properly-parsed tables.
    unmarked = strip_continuation_marks(text)
    lines = unmarked.splitlines()
    if len(lines) > 1 and any(line.lstrip().startswith("|") for line in lines):
        return unmarked

    opts = _plugin_options(context)

    min_chars = opts.get("min_chars", DEFAULT_MIN_CHARS)
    abbreviations = opts.get("abbreviations", None)
    break_clauses = bool(opts.get("break_clauses", False))
    clause_chars = opts.get("clause_chars", DEFAULT_CLAUSE_CHARS)
    closing_punct = bool(opts.get("closing_punct", False))
    # Zero means "never indent": with the option off no break is marked, and
    # no line may gain a continuation indent.
    continuation_indent = (
        int(opts.get("continuation_indent", DEFAULT_CONTINUATION_INDENT))
        if bool(opts.get("preserve_indented_breaks", False))
        else 0
    )

    return insert_breaks(
        text,
        min_chars=int(min_chars),
        abbreviations=abbreviations,
        break_clauses=break_clauses,
        clause_chars=clause_chars,
        closing_punct=closing_punct,
        continuation_indent=continuation_indent,
    )


def add_cli_argument_group(group: argparse._ArgumentGroup) -> None:
    """Register CLI options, mirrored to TOML ``[plugin.sembr]``.

    Values are stored under ``mdit.options["mdformat"]["plugin"]["sembr"]`` and
    merged with the TOML config. ``dest`` names deliberately match the TOML keys
    so CLI values merge cleanly over TOML.
    """
    group.add_argument(
        "--sembr-min-chars",
        dest="min_chars",
        type=int,
        default=None,
        metavar="N",
        help=(
            "minimum length of the segment before a break is allowed "
            f"(default: {DEFAULT_MIN_CHARS})"
        ),
    )
    group.add_argument(
        "--sembr-abbreviations",
        dest="abbreviations",
        action="append",
        default=None,
        metavar="ABBR",
        help=(
            "abbreviation after which no sentence break is inserted; "
            "repeat to add several (replaces the default list)"
        ),
    )
    group.add_argument(
        "--sembr-break-clauses",
        dest="break_clauses",
        action="store_true",
        default=None,
        help="also break after clause punctuation (Iteration 2; off by default)",
    )
    group.add_argument(
        "--sembr-clause-chars",
        dest="clause_chars",
        default=None,
        metavar="CHARS",
        help=(
            "clause punctuation set used when --sembr-break-clauses is on "
            f"(default: {DEFAULT_CLAUSE_CHARS!r})"
        ),
    )
    group.add_argument(
        "--sembr-closing-punct",
        dest="closing_punct",
        action="store_true",
        default=None,
        help=(
            "handle closing punctuation (quotes, brackets) after sentence "
            "terminators, keeping them on the preceding line — intended for "
            "American-English punctuation style (off by default)"
        ),
    )
    group.add_argument(
        "--sembr-preserve-indented-breaks",
        dest="preserve_indented_breaks",
        action="store_true",
        default=None,
        help=(
            "keep a soft break the author pinned by indenting the line after "
            "it, instead of reflowing it away (SemBr rules 6 and 8; off by "
            "default)"
        ),
    )
    group.add_argument(
        "--sembr-continuation-indent",
        dest="continuation_indent",
        type=int,
        default=None,
        metavar="N",
        help=(
            "spaces per level of continuation indent, used to read and to "
            "re-emit pinned breaks "
            f"(default: {DEFAULT_CONTINUATION_INDENT})"
        ),
    )


#: A mapping from ``RenderTreeNode.type`` to a ``Render`` function. Empty: we do
#: not override rendering.
RENDERERS: Mapping[str, Any] = {}

#: A mapping from ``RenderTreeNode.type`` to a collaborative ``Postprocess``.
#: The break types are handled here rather than in ``RENDERERS`` deliberately:
#: postprocessors chain, so this cannot conflict with another plugin, and it
#: leaves mdformat's own break rendering in place.
POSTPROCESSORS: Mapping[str, Any] = {
    "paragraph": _postprocess_paragraph,
    "softbreak": _postprocess_break,
    "hardbreak": _postprocess_break,
}


__all__ = [
    "CHANGES_AST",
    "RENDERERS",
    "POSTPROCESSORS",
    "update_mdit",
    "add_cli_argument_group",
    "DEFAULT_ABBREVIATIONS",
    "DEFAULT_CLAUSE_CHARS",
    "DEFAULT_CONTINUATION_INDENT",
    "DEFAULT_MIN_CHARS",
]
