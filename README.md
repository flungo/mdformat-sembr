# mdformat-sembr

> **Canonical home:** [codeberg.org/adel/mdformat-sembr](https://codeberg.org/adel/mdformat-sembr) — issues and contributions tracked there.
> The [GitHub mirror](https://github.com/adel/mdformat-sembr) exists solely to enable PyPI Trusted Publishing and provenance attestation.

> **⚠️ Disclaimer**: This project was built using **vibe-/agentic-coding**

An [mdformat](https://mdformat.readthedocs.io) parser-extension plugin that inserts
[Semantic Line Breaks](https://sembr.org) (SemBr) as CommonMark **soft breaks**.

SemBr is a convention for adding line breaks in Markdown source at sentence and
clause boundaries. Because the breaks are CommonMark *soft* breaks (a bare `\n`
inside a paragraph), they render to a single space — the rendered HTML output is
unchanged, only the source becomes more diff-friendly.

The plugin is fully deterministic: no ML, no network, no LLM calls. The same input
always produces the same output.

## Why

Moving SemBr logic out of an LLM/agent loop into a token-free, reproducible
formatter pass makes authored Markdown consistent and cheap to maintain.

## Install

```bash
# uv (recommended) — --with is repeatable (or comma-separate the plugins)
uv tool install mdformat --with mdformat-sembr --with mdformat-frontmatter

# pipx — install the app, then inject the plugins into its environment
pipx install mdformat
pipx inject mdformat mdformat-sembr mdformat-frontmatter

# local development
pip install -e .
```

`mdformat-frontmatter` is optional: install it only if your Markdown uses
YAML/TOML frontmatter and you want mdformat to preserve/format it. It composes
with `mdformat-sembr` (frontmatter is a separate node type and is never broken).

## Usage

```bash
mdformat --version          # should list "mdformat_sembr"
echo "First sentence. Second sentence." | mdformat -
```

From Python:

```python
import mdformat

mdformat.text("First sentence. Second sentence.\n", extensions={"sembr"})
# 'First sentence.\nSecond sentence.\n'
```

## How it works

The plugin registers a **postprocessor** on the `paragraph` node type. At that point
inline formatting (emphasis, links, inline code) is already resolved into the string,
so it operates on the final rendered text and only protects a few inline constructs by
regex. Block-level elements (headings, code blocks, tables, frontmatter, HTML blocks)
are separate node types and are never touched.

`CHANGES_AST = False`: soft breaks are AST-safe by design, so mdformat's built-in
`is_md_equal` validator gates correctness. If validation ever fails, the break logic is
wrong — it is never worked around with `--no-validate` or hard breaks.

Two further hooks exist only to serve `preserve_indented_breaks`, and do nothing
when it is off. markdown-it discards the indentation of a continuation line while
tokenizing, so the plugin wraps the `newline` and `escape` inline rules to record it
as token metadata — metadata the HTML renderer ignores, so validation is unaffected.
A postprocessor on the `softbreak` and `hardbreak` node types then re-attaches it as
an internal marker that the paragraph postprocessor consumes. Break handling lives in
`POSTPROCESSORS` rather than `RENDERERS` so that it chains with other plugins instead
of conflicting with them.

## Configuration

Configure via `[plugin.sembr]` in `.mdformat.toml`, or via CLI flags. CLI values merge
over TOML.

| Option                      | Type      | Default   | Meaning                                                                 |
| --------------------------- | --------- | --------- | ----------------------------------------------------------------------- |
| `min_chars`                 | int       | `15`      | Minimum length of the segment before a break is allowed.                |
| `abbreviations`             | list[str] | see below | Tokens after which no sentence break is inserted.                       |
| `break_clauses`             | bool      | `false`   | Enable clause-level breaks (SemBr "SHOULD"). Off by default.            |
| `clause_chars`              | str       | `",;:—"`  | Clause punctuation set (only used when `break_clauses` true).           |
| `closing_punct`             | bool      | `false`   | Keep a closing quote or bracket after a terminator on the same line.    |
| `preserve_indented_breaks`  | bool      | `false`   | Keep breaks the author pinned by indenting the next line.               |
| `continuation_indent`       | int       | `2`       | Spaces per level of continuation indent (read and re-emitted).          |

CLI flags: `--sembr-min-chars`, `--sembr-abbreviations`, `--sembr-break-clauses`,
`--sembr-clause-chars`, `--sembr-closing-punct`,
`--sembr-preserve-indented-breaks`, `--sembr-continuation-indent`.

`.mdformat.toml` example:

```toml
[plugin.sembr]
min_chars = 20
break_clauses = true
preserve_indented_breaks = true
```

### Optional breaks (`preserve_indented_breaks`)

The plugin can insert the breaks SemBr rule 4 requires and rule 5 recommends,
because those follow from punctuation. Rules 6, 8, 10 and 11 are *MAY* rules —
a break after a dependent clause, between grouped list items, or around a link —
and nothing in the text says where they belong. They exist only because an
author chose them, so the only way a formatter can honour them is to keep the
ones already there.

By default every break is collapsed and the paragraph reflowed, which removes
them. With `preserve_indented_breaks` on, a break is kept when the line after it
is **indented**, and the indentation is re-emitted at `continuation_indent`
spaces per level:

```markdown
All human beings are born free and equal in dignity and rights.
They are endowed with reason and conscience
  and should act towards one another in a spirit of brotherhood.
A list of items:
  the first item in the list;
  the second item in the list;
  and the final item of that list.
Before another sentence.
```

That document is already formatted: `mdformat` leaves it exactly as it is.

Indentation is used as the signal rather than punctuation because it is the one
that actually discriminates. The break after "conscience" above is SemBr's own
introductory example and no punctuation precedes it, so a punctuation guard
would reject it; meanwhile a document hard-wrapped at a column width scatters
breaks after commas, which a punctuation guard would then mistake for intent.
Indentation is never accidental.

Some consequences worth knowing:

- **Everything else still reflows.** A pinned break stops a collapse, not the
  formatter: text inside a pinned group is broken at sentence and clause
  boundaries as usual, and unindented breaks are reflowed as they always were.
- **`min_chars` does not apply** to a pinned break. An explicit choice outranks
  the heuristic that suppresses short segments.
- **Nesting is preserved.** Deeper indentation is a deeper level; an odd width
  snaps down to the level below, and a tab counts as one column.
- **Enabling it is safe for existing documents.** Output SemBr has already
  formatted carries no indentation, so there is nothing to pin and nothing
  moves.
- **Indentation is invisible to readers.** CommonMark strips leading whitespace
  from a paragraph's continuation lines, so this satisfies SemBr rule 2 and
  mdformat's `is_md_equal` validator. See below for how far that is verified.

### Render neutrality

The claim that continuation indentation cannot change the rendered output is
checked by sweep, not by argument. `tests/test_render_neutrality.py` formats
every combination of

- **22 containers** — top-level paragraphs; `-`/`*`/`+` bullets; `1.`/`1)`/`100.`
  ordered items; lists nested two and three deep; loose list items; block quotes,
  nested block quotes and lazy quote lines; lists in quotes and quotes in lists;
  second paragraphs of a list item; across emphasis, strong and link text; and
  after both spellings of a hard break;
- **15 continuation payloads**, all but one of which would start a *new* block if
  they were not indented — bullet and ordered markers, ATX headings, quote
  markers, fences, setext underlines, thematic breaks, HTML blocks, table rows
  and escaped punctuation;
- **6 authored indent widths** × **4 `continuation_indent` settings**,

and asserts `is_md_equal` and idempotency on each. Cases where the line parses as
its own block are skipped, since the plugin never sees those.

Two mdformat behaviours make this less obvious than it looks, and both are
regression-tested:

- mdformat's paragraph renderer escapes a line that could open a block (`#`, `>`,
  `-`, `1.`, `---`), but **every check is anchored at column zero**. The
  continuation marker therefore ends the line a break *closes*; at the start of
  the line it opens, it would hide the first character and silently suppress the
  escape.
- An HTML block is neutralised by **indenting the line four spaces** rather than
  escaping it — the one construct mdformat guards with whitespace. Incoming
  indentation is therefore a floor: re-indenting never emits less than the line
  arrived with.

Four contexts cannot carry a pinned break. All of them stay AST-safe and
idempotent; they just reflow as they did before:

| Context | Why |
| --- | --- |
| Setext headings | A heading folds to a single line; it has no continuation lines. |
| A `\|`-leading line with no table plugin | The no-table-plugin fallback leaves the block verbatim. |
| Newline inside a code span | Not a soft break — the newline belongs to the code span. |
| Newline inside inline HTML | Not a soft break, and mdformat must keep it to stay inline. |

## Releasing

The version is not written down anywhere. It is derived from the git tag at
build time by [`uv-dynamic-versioning`](https://github.com/ninoseki/uv-dynamic-versioning),
and `mdformat_sembr.__version__` reads it back from the installed
distribution's metadata — so there is no second copy to drift.

To cut a release, tag the commit and push the tag:

```bash
git tag v0.3.0
git push origin v0.3.0
```

Between tags the version is a PEP 440 development version such as
`0.2.0.post2.dev0+8174d69`. Building outside a git checkout is an error rather
than a `0.0.0` fallback, and the publish workflow both checks out full history
(a shallow clone cannot resolve the tag) and refuses to publish an artifact
whose version does not match the tag.

## License

MIT
