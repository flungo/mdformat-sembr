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
import warnings
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from markdown_it.rules_inline import escape as _default_escape_rule
from markdown_it.rules_inline import newline as _default_newline_rule

from mdformat_sembr._sembr import (
    BREAK_MARK,
    CONTINUATION_MARK,
    DEFAULT_ABBREVIATIONS,
    DEFAULT_CLAUSE_CHARS,
    DEFAULT_CONTINUATION_INDENT,
    DEFAULT_MIN_CHARS,
    insert_breaks,
    strip_marks,
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
                    # The key is prefixed because ``meta`` is shared with every
                    # other plugin that touches the token; the value is the raw
                    # width, with nothing read into it yet.
                    token.meta = {**token.meta, "sembr_indent": indent}
        return handled

    return tracked


def update_mdit(mdit: "MarkdownIt") -> None:
    """Tag line breaks with the indentation of the line they open.

    Both rules matter: ``newline`` produces soft breaks and the two-space hard
    break, while a backslash hard break comes out of ``escape``.
    """
    # The only hook that runs once per document and can see the merged options,
    # so it is also where a configuration that cannot work is reported. The
    # options are absent when a parser is built by hand rather than by mdformat,
    # which is no configuration at all and so nothing to complain about.
    _warn_if_preserving_has_nowhere_to_record(
        _sembr_options(mdit.options.get("mdformat", {}))
    )

    mdit.inline.ruler.at("newline", _track_indent(_default_newline_rule))
    mdit.inline.ruler.at("escape", _track_indent(_default_escape_rule))


def _sembr_options(mdformat_opts: Mapping[str, Any]) -> Mapping[str, Any]:
    """Return the merged ``[plugin.sembr]`` / CLI options mapping."""
    return mdformat_opts.get("plugin", {}).get("sembr", {}) or {}


def _plugin_options(context: "RenderContext") -> Mapping[str, Any]:
    """Return the merged ``[plugin.sembr]`` / CLI options for a render."""
    return _sembr_options(context.options.get("mdformat", {}))


def _preserve_indented_breaks(opts: Mapping[str, Any]) -> bool:
    """Whether an indented line in the *source* marks a break to keep.

    This is the half that cannot be re-derived, so it is opt-in on its own.
    Indenting the output is not: a clause break is recovered from its
    punctuation on every run whether or not anything was marked, so a reader of
    ``break_clauses`` can have the indentation as pure style — and removing the
    punctuation removes the break again, indent and all.
    """
    return bool(opts.get("preserve_indented_breaks", False))


def _continuation_indent(opts: Mapping[str, Any]) -> int:
    """Spaces a continuation line is indented by; zero turns indenting off.

    Preserving a break needs somewhere to record it, and the indent of the line
    below is the only place there is — so ``preserve_indented_breaks`` raises
    this default off zero. Setting it to zero explicitly turns preservation off
    along with the indenting, rather than keeping breaks it cannot record.
    """
    default = DEFAULT_CONTINUATION_INDENT if _preserve_indented_breaks(opts) else 0
    return int(opts.get("continuation_indent", default))


def _warn_if_preserving_has_nowhere_to_record(opts: Mapping[str, Any]) -> None:
    """Warn when preservation is asked for with nowhere to record what it keeps.

    A kept break is recorded by indenting the line below it, and that indent is
    also how the next run recognises it. At zero width there is nothing to write
    and therefore nothing to read back, so preserving would emit a document the
    next run undoes. Preservation is off instead — and saying so out loud beats
    leaving an author believing their breaks are protected when they are being
    reflowed away.

    mdformat gives a parser extension no hook for validating configuration, so
    this is a warning rather than an error: refusing to format a file over a
    contradiction the plugin can resolve safely would be the worse trade.
    """
    if _preserve_indented_breaks(opts) and not _continuation_indent(opts):
        warnings.warn(
            "mdformat-sembr: preserve_indented_breaks is ignored while "
            "continuation_indent is 0, because a preserved break is recorded by "
            "indenting the line below it. Set continuation_indent to a "
            "non-zero width, or drop preserve_indented_breaks.",
            UserWarning,
        )


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
    """Mark a rendered line break, recording whether it opens a continuation.

    The marker is consumed again by :func:`_postprocess_paragraph`, so it never
    reaches the output. It also never reaches ``is_md_equal``, which renders
    through markdown-it's HTML renderer rather than mdformat's.

    Every break is marked, not only the indented ones: that is what lets the
    paragraph postprocessor tell a break the author wrote from a newline
    mdformat's own word wrapping introduced.

    The marker precedes the break rather than starting the line it opens — see
    :data:`BREAK_MARK` for why that distinction matters.
    """
    opts = _plugin_options(context)
    if not _continuation_indent(opts):
        return text
    if not _in_paragraph(node):
        return text

    # Any indentation at all makes the line a continuation, whatever its width:
    # the signal is yes-or-no, not a depth. A continuation that would want a
    # second level is, in practice, a new sentence, and a new sentence starts at
    # the margin.
    #
    # Reading it at all is ``preserve_indented_breaks``' job. Without that, an
    # indented line is marked as an ordinary break and collapses like any other,
    # so a break the rules cannot re-derive is removed — which is the point: the
    # indentation the plugin itself wrote must not turn into a reason to keep a
    # break whose punctuation the author has since deleted.
    indented = bool(node.meta.get("sembr_indent", 0)) and _preserve_indented_breaks(
        opts
    )
    # The marker goes immediately before the break — ahead of a hard break's
    # own backslash, and never at the start of the line the break opens; see
    # BREAK_MARK for why that placement is load-bearing. Keeping it ahead of the
    # backslash also leaves the hard-break split untouched.
    #
    # Under ``--wrap N`` mdformat renders a soft break as a wrap marker rather
    # than a newline; an author's break outranks word wrapping, so restore it.
    mark = CONTINUATION_MARK if indented else BREAK_MARK
    return mark + (text if text.endswith("\n") else "\n")


def _postprocess_paragraph(
    text: str,
    node: "RenderTreeNode",
    context: "RenderContext",
) -> str:
    """Insert SemBr soft breaks into an already-rendered paragraph string."""
    opts = _plugin_options(context)
    # With the feature off no marker was written, so there is nothing to strip
    # and a control character in the text can only be one the document held.
    unmarked = strip_marks(text) if _continuation_indent(opts) else text

    # When no GFM table plugin is active, markdown-it parses tables as
    # paragraphs. Detect that case (multiple lines where at least one starts
    # with '|') and return the text unchanged so the table structure is
    # preserved. With a table plugin the node type is 'table', not 'paragraph',
    # so this guard is never reached for properly-parsed tables.
    lines = unmarked.splitlines()
    if len(lines) > 1 and any(line.lstrip().startswith("|") for line in lines):
        return unmarked

    min_chars = opts.get("min_chars", DEFAULT_MIN_CHARS)
    abbreviations = opts.get("abbreviations", None)
    break_clauses = bool(opts.get("break_clauses", False))
    clause_chars = opts.get("clause_chars", DEFAULT_CLAUSE_CHARS)
    closing_punct = bool(opts.get("closing_punct", False))

    return insert_breaks(
        text,
        min_chars=int(min_chars),
        abbreviations=abbreviations,
        break_clauses=break_clauses,
        clause_chars=clause_chars,
        closing_punct=closing_punct,
        continuation_indent=_continuation_indent(opts),
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
        "--sembr-continuation-indent",
        dest="continuation_indent",
        type=int,
        default=None,
        metavar="N",
        help=(
            "indent a line that continues the one above by N spaces (0 to not "
            "indent; default: 0, or "
            f"{DEFAULT_CONTINUATION_INDENT} with "
            "--sembr-preserve-indented-breaks)"
        ),
    )
    group.add_argument(
        "--sembr-preserve-indented-breaks",
        dest="preserve_indented_breaks",
        action="store_true",
        default=None,
        help=(
            "treat an indented line in the source as a break the author meant "
            "to keep, instead of reflowing it away (SemBr rules 6, 8, 10 and "
            "11; off by default)"
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
