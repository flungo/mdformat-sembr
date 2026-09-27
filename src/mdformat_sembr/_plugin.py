"""mdformat parser-extension interface for the SemBr plugin.

This module *is* the plugin interface object referenced by the entry point
``mdformat_sembr:_plugin``. It exposes the members required by
``mdformat.plugins.ParserExtensionInterface`` at module level.

We do not change the parser or override any renderer; all work happens in a
postprocessor registered on the ``paragraph`` node type. At that point inline
formatting is already resolved into the rendered string, so we operate on final
text and protect a few inline constructs by regex.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from mdformat_sembr._sembr import (
    DEFAULT_ABBREVIATIONS,
    DEFAULT_CLAUSE_CHARS,
    DEFAULT_MIN_CHARS,
    insert_breaks,
)

if TYPE_CHECKING:
    from markdown_it import MarkdownIt
    from mdformat.renderer import RenderContext, RenderTreeNode

#: SemBr soft breaks never alter the rendered output, so the AST is unchanged.
#: This lets mdformat's built-in ``is_md_equal`` validator gate correctness.
CHANGES_AST = False


def update_mdit(mdit: "MarkdownIt") -> None:
    """No parser change is needed for SemBr."""
    # Intentionally a no-op.
    pass


def _plugin_options(context: "RenderContext") -> Mapping[str, Any]:
    """Return the merged ``[plugin.sembr]`` / CLI options mapping."""
    mdformat_opts = context.options.get("mdformat", {})
    plugin_opts = mdformat_opts.get("plugin", {})
    return plugin_opts.get("sembr", {}) or {}


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
    lines = text.splitlines()
    if len(lines) > 1 and any(line.lstrip().startswith("|") for line in lines):
        return text

    opts = _plugin_options(context)

    min_chars = opts.get("min_chars", DEFAULT_MIN_CHARS)
    sentence_min_chars = opts.get("sentence_min_chars", None)
    clause_min_chars = opts.get("clause_min_chars", None)
    abbreviations = opts.get("abbreviations", None)
    break_clauses = bool(opts.get("break_clauses", False))
    clause_chars = opts.get("clause_chars", DEFAULT_CLAUSE_CHARS)
    closing_punct = bool(opts.get("closing_punct", False))

    return insert_breaks(
        text,
        min_chars=int(min_chars),
        sentence_min_chars=(
            None if sentence_min_chars is None else int(sentence_min_chars)
        ),
        clause_min_chars=None if clause_min_chars is None else int(clause_min_chars),
        abbreviations=abbreviations,
        break_clauses=break_clauses,
        clause_chars=clause_chars,
        closing_punct=closing_punct,
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
            f"(default: {DEFAULT_MIN_CHARS}); the fallback for "
            "--sembr-sentence-min-chars and --sembr-clause-min-chars"
        ),
    )
    group.add_argument(
        "--sembr-sentence-min-chars",
        dest="sentence_min_chars",
        type=int,
        default=None,
        metavar="N",
        help=(
            "minimum length of the segment before a sentence break is allowed "
            "(default: --sembr-min-chars)"
        ),
    )
    group.add_argument(
        "--sembr-clause-min-chars",
        dest="clause_min_chars",
        type=int,
        default=None,
        metavar="N",
        help=(
            "minimum length of the segment before a clause break is allowed "
            "(default: --sembr-min-chars)"
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


#: A mapping from ``RenderTreeNode.type`` to a ``Render`` function. Empty: we do
#: not override rendering.
RENDERERS: Mapping[str, Any] = {}

#: A mapping from ``RenderTreeNode.type`` to a collaborative ``Postprocess``.
POSTPROCESSORS: Mapping[str, Any] = {"paragraph": _postprocess_paragraph}


__all__ = [
    "CHANGES_AST",
    "RENDERERS",
    "POSTPROCESSORS",
    "update_mdit",
    "add_cli_argument_group",
    "DEFAULT_ABBREVIATIONS",
    "DEFAULT_CLAUSE_CHARS",
    "DEFAULT_MIN_CHARS",
]
