"""mdformat parser-extension interface for the SemBr plugin.

This module *is* the plugin interface object referenced by the entry point
``mdformat_sembr:_plugin``. It exposes the members required by
``mdformat.plugins.ParserExtensionInterface`` at module level.

We override no renderer; all work happens in postprocessors, chiefly the one on
the ``paragraph`` node type. At that point inline formatting is already resolved
into the rendered string, so we operate on final text and protect a few inline
constructs by regex.

With ``preserve_indented_breaks`` set, and only then, the parser is touched to
keep information it would otherwise discard: two inline rules are wrapped so
that line breaks remember how far the line they open was indented. That is
metadata, invisible to rendering.
"""

from __future__ import annotations

import argparse
import re
import warnings
from collections.abc import Iterator, Mapping
from typing import TYPE_CHECKING, Any

from mdformat_sembr._sembr import (
    BREAK_MARK,
    CONTINUATION_MARK,
    DEFAULT_ABBREVIATIONS,
    DEFAULT_CLAUSE_CHARS,
    DEFAULT_CONTINUATION_INDENT,
    DEFAULT_MIN_CHARS,
    UNGUARDED_STARTS,
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

#: What the break postprocessor writes after each break it marks: a line holding
#: nothing but one of these, naming the kind of break and whether the line after
#: it was indented.
#:
#: A line of its own, because mdformat's paragraph renderer runs a line-safety
#: pass over the text before any postprocessor sees it — escaping a line that
#: could open a block, such as ``#``, ``>``, ``-`` or ``1.`` — and those checks
#: read the whole line. Some are anchored at its end, as a bare ``1.`` or ``-``
#: opens a list too, and every line is stripped of whitespace first. A marker
#: anywhere on a line of text defeats one or the other, and the escape is
#: silently lost. On a line by itself it trips none of them, and every real
#: line is checked exactly as it would be without the plugin.
#:
#: Soft and hard breaks need different characters because the line before a
#: soft break can itself end in a backslash: an escaped one.
_MARK_LINES = {
    ("softbreak", False): "\x03",
    ("softbreak", True): "\x04",
    ("hardbreak", False): "\x05",
    ("hardbreak", True): "\x06",
}
_SOFT_MARK_LINE_RE = re.compile(r"\n([\x03\x04])\n")
_HARD_MARK_LINE_RE = re.compile(r"\\\n([\x05\x06])\n")
_IN_LINE_MARK = {
    "\x03": BREAK_MARK,
    "\x04": CONTINUATION_MARK,
    "\x05": BREAK_MARK,
    "\x06": CONTINUATION_MARK,
}

#: Every character the marking uses, on a line of its own or folded in. No
#: Markdown construct produces one, but a document can still hold one literally,
#: and a paragraph that does is not marked at all, since nothing could then tell
#: the plugin's characters from the document's.
_PROTOCOL_CHARS = frozenset((BREAK_MARK, CONTINUATION_MARK, *_MARK_LINES.values()))


def _fold_mark_lines(text: str) -> str:
    """Move each marker line onto the end of the line before it.

    That is where :func:`insert_breaks` reads a marker, now that mdformat's
    line-safety pass is done with the text. A hard break's goes before its
    backslash, where the hard-break split does not see it.
    """
    text = _HARD_MARK_LINE_RE.sub(lambda m: _IN_LINE_MARK[m[1]] + "\\\n", text)
    return _SOFT_MARK_LINE_RE.sub(lambda m: _IN_LINE_MARK[m[1]] + "\n", text)


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

    Only ``preserve_indented_breaks`` reads that, so without it nothing is
    installed and the parser is left exactly as mdformat built it. Both rules
    matter: ``newline`` produces soft breaks and the two-space hard break, while
    a backslash hard break comes out of ``escape``. Each is wrapped as currently
    registered, so a plugin loaded earlier that replaced one keeps its version.

    This is also where the one configuration that cannot work is reported: it
    is the only hook that sees the merged options before anything is parsed.
    """
    # The options are absent when a parser is built by hand rather than by
    # mdformat, which is no configuration at all.
    opts = _sembr_options(mdit.options.get("mdformat", {}))
    _warn_if_preserving_has_nowhere_to_record(opts)
    if not _reading(opts):
        return

    ruler = mdit.inline.ruler
    for name in ("newline", "escape"):
        index = ruler.__find__(name)
        if index != -1:
            rule = ruler.__rules__[index]
            # ``at`` resets the chains a rule also runs in unless told them.
            ruler.at(name, _track_indent(rule.fn), {"alt": rule.alt})


def _sembr_options(mdformat_opts: Mapping[str, Any]) -> Mapping[str, Any]:
    """Return the merged ``[plugin.sembr]`` / CLI options mapping."""
    return mdformat_opts.get("plugin", {}).get("sembr", {}) or {}


def _plugin_options(context: "RenderContext") -> Mapping[str, Any]:
    """Return the merged ``[plugin.sembr]`` / CLI options for a render."""
    return _sembr_options(context.options.get("mdformat", {}))


def _preserve_indented_breaks(opts: Mapping[str, Any]) -> bool:
    """Whether an indented line in the *source* marks a break to keep.

    That is the half no rule can re-derive, so it is an opt-in of its own,
    separate from indenting the output. A clause break comes back from its
    punctuation on every run, marked or not, so someone using ``break_clauses``
    can have the indentation as style alone — and deleting the punctuation
    still deletes the break, indent and all.
    """
    return bool(opts.get("preserve_indented_breaks", False))


def _continuation_indent(opts: Mapping[str, Any]) -> int:
    """Spaces a continuation line is indented by; zero turns indenting off.

    Preserving a break needs somewhere to record it, and the indent of the line
    below is the only place there is — so ``preserve_indented_breaks`` raises
    this default off zero. Setting it to zero explicitly turns preservation off
    along with the indenting, rather than keeping breaks it cannot record. A
    negative width indents nothing either, so it counts as zero.
    """
    default = DEFAULT_CONTINUATION_INDENT if _preserve_indented_breaks(opts) else 0
    return max(0, int(opts.get("continuation_indent", default)))


def _reading(opts: Mapping[str, Any]) -> bool:
    """Whether breaks are marked and read back: preservation, with a width."""
    return _preserve_indented_breaks(opts) and _continuation_indent(opts) > 0


def _marking(paragraph: "RenderTreeNode", context: "RenderContext") -> bool:
    """Whether this paragraph's breaks are marked and read back.

    A paragraph that is not is reflowed as if only ``continuation_indent`` were
    set, which is safe for any text at all, but keeps none of its breaks. That
    is one already holding a character the marking uses, where nothing could
    tell the two apart, and one the table fallback leaves as mdformat gives it:
    its kept lines would reach the output without the guards a reflow gives
    them, where word wrapping would have joined them.
    """
    if not _reading(_plugin_options(context)):
        return False
    # Every break in the paragraph asks, and the answer means reading all of
    # it, so it is worked out once per paragraph for the length of the render.
    known = context.env.setdefault("sembr_marking", {})
    if paragraph not in known:
        source = "".join(child.content for child in paragraph.children)
        known[paragraph] = _PROTOCOL_CHARS.isdisjoint(
            source
        ) and not _taken_for_a_table(source)
    return known[paragraph]


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
            "continuation_indent is not a positive width, because a preserved "
            "break is recorded by indenting the line below it. Set "
            "continuation_indent above 0, or drop preserve_indented_breaks.",
            UserWarning,
        )


def _enclosing_paragraph(node: "RenderTreeNode") -> "RenderTreeNode | None":
    """Return the paragraph ``node`` renders in, or None, as in a heading."""
    parent = node.parent
    while parent is not None:
        if parent.type in ("paragraph", "heading"):
            return parent if parent.type == "paragraph" else None
        parent = parent.parent
    return None


def _position(
    node: "RenderTreeNode", context: "RenderContext"
) -> tuple[list["RenderTreeNode"], int]:
    """Return ``node``'s siblings and where it stands among them.

    markdown-it's own sibling links search the parent's children on every step,
    which a paragraph of many lines makes slow, so the positions are worked out
    once per parent for the length of the render.
    """
    parent = node.parent
    known = context.env.setdefault("sembr_positions", {})
    if parent not in known:
        known[parent] = {child: i for i, child in enumerate(parent.children)}
    return parent.children, known[parent][node]


def _in_image(node: "RenderTreeNode") -> bool:
    """Return True if ``node`` is part of an image's description."""
    parent = node.parent
    while parent is not None and parent.type != "paragraph":
        if parent.type == "image":
            return True
        parent = parent.parent
    return False


def _following(
    node: "RenderTreeNode", context: "RenderContext"
) -> Iterator["RenderTreeNode"]:
    """Yield what comes after ``node`` in its paragraph, nearest first.

    A node is yielded instead of its children, which is all the callers need.
    """
    while node.type != "paragraph":
        siblings, index = _position(node, context)
        for later in range(index + 1, len(siblings)):
            yield siblings[later]
        node = node.parent


def _preceding(
    node: "RenderTreeNode", context: "RenderContext"
) -> Iterator["RenderTreeNode"]:
    """Yield what comes before ``node`` in its paragraph, nearest first."""
    while node.type != "paragraph":
        siblings, index = _position(node, context)
        for earlier in range(index - 1, -1, -1):
            yield siblings[earlier]
        node = node.parent


def _holds_text(node: "RenderTreeNode") -> bool:
    """Whether ``node`` renders to more than whitespace."""
    if node.type in ("text", *_BREAK_TOKENS):
        return bool(node.content.strip())
    return True


def _borders_an_empty_line(node: "RenderTreeNode", context: "RenderContext") -> bool:
    """Whether the line the break ``node`` opens, or the one it closes, holds
    nothing but whitespace.

    mdformat strips every line of whitespace, so such a line comes out empty.
    """
    return _line_is_empty(_following(node, context)) or _line_is_empty(
        _preceding(node, context)
    )


def _line_is_empty(nodes: Iterator["RenderTreeNode"]) -> bool:
    """Whether ``nodes``, walking away from a break, reach another break or the
    paragraph's edge before anything but whitespace."""
    for node in nodes:
        if node.type in _BREAK_TOKENS:
            return True
        if _holds_text(node):
            return False
    return True


def _opens_an_unguarded_line(node: "RenderTreeNode", context: "RenderContext") -> bool:
    """Whether the line the break ``node`` opens starts with a character that
    is never indented (see :data:`UNGUARDED_STARTS`)."""
    for after in _following(node, context):
        if after.type != "text":
            return False
        text = after.content.lstrip()
        if text:
            return text[0] in UNGUARDED_STARTS
    return False


def _postprocess_break(
    text: str,
    node: "RenderTreeNode",
    context: "RenderContext",
) -> str:
    """Follow a rendered line break with a line saying what kind it was.

    :func:`_postprocess_paragraph` folds that line away again (see
    :data:`_MARK_LINES`), so it never reaches the output. It never reaches
    ``is_md_equal`` either, which renders through markdown-it's HTML renderer
    rather than mdformat's.

    Every break is marked, not only the indented ones, except the few below
    that could keep nothing: that is what lets the paragraph postprocessor tell
    a break the author wrote from a newline that mdformat's word wrapping or a
    multi-line inline construct put there.

    Nothing is written unless breaks are being read back. Indenting the output
    alone needs no record of the source: every break it indents is one the
    break rules re-derive on each run.
    """
    if not _reading(_plugin_options(context)):
        return text
    paragraph = _enclosing_paragraph(node)
    if paragraph is None or not _marking(paragraph, context):
        return text
    if _in_image(node):
        # An image's description is rendered as plain text, without the escapes
        # mdformat gives the lines of a paragraph, so a break kept there can
        # make markup of what it leaves at the end of a line: a backslash
        # becomes a hard break. A hard break there mdformat writes as nothing.
        return text
    if _borders_an_empty_line(node, context):
        # An empty line marks nothing, and a marker beside one goes astray: at
        # either end of the paragraph, whose outermost whitespace mdformat
        # writes as an entity, newline included, the fold cannot find it; and
        # anywhere else it is folded onto the empty line, which a table-like
        # paragraph keeps, or which hides the whitespace set aside after a hard
        # break. Left unmarked, the break is reflowed as it is without reading.
        return text
    if _opens_an_unguarded_line(node, context):
        # That line is never indented, so it could not record a kept break.
        return text

    # Any indentation at all makes the line a continuation, whatever its width:
    # the signal is yes-or-no, not a depth. A continuation that would want a
    # second level is, in practice, a new sentence, and a new sentence starts at
    # the margin.
    indented = bool(node.meta.get("sembr_indent", 0))
    # Under ``--wrap N`` mdformat renders a soft break as a wrap point rather
    # than a newline; a break the author wrote outranks word wrapping, so it is
    # restored.
    newline = text if text.endswith("\n") else "\n"
    return newline + _MARK_LINES[node.type, indented] + "\n"


def _taken_for_a_table(text: str) -> bool:
    """Whether the paragraph postprocessor leaves ``text`` as it is: whether it
    has several lines, one of them starting with a pipe."""
    lines = text.splitlines()
    return len(lines) > 1 and any(line.lstrip().startswith("|") for line in lines)


def _postprocess_paragraph(
    text: str,
    node: "RenderTreeNode",
    context: "RenderContext",
) -> str:
    """Insert SemBr soft breaks into an already-rendered paragraph string."""
    opts = _plugin_options(context)
    reading = _marking(node, context)
    if reading:
        text = _fold_mark_lines(text)
    # Without reading, no marker was written, so there is nothing to strip, and
    # a control character in the text can only be one the document held.
    unmarked = strip_marks(text) if reading else text

    # When no GFM table plugin is active, markdown-it parses tables as
    # paragraphs. Detect that case (multiple lines where at least one starts
    # with '|') and return the text unchanged so the table structure is
    # preserved. With a table plugin the node type is 'table', not 'paragraph',
    # so this guard is never reached for properly-parsed tables.
    if _taken_for_a_table(unmarked):
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
        marked=reading,
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
