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
an internal marker, written just before the break so that neither mdformat's
line-start escaping nor the hard-break split is disturbed, and the paragraph
postprocessor consumes it. Break handling lives in
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
is **indented**, and that line is re-emitted at one `continuation_indent`:

```markdown
The release notes are generated from the commit log,
  filtered to the commits that touch a public interface.
Each entry takes one of three shapes:
  a feature, described by its commit subject;
  a fix, with the issue number it closes;
  and a breaking change, with a migration note.
Everything else is left out.
```

That document is already formatted: `mdformat` leaves it exactly as it is. The
second line is SemBr rule 6, a dependent clause that qualifies the sentence
above it; the three indented lines are rule 8, list items grouped under the
sentence that introduces them. Neither is a break the plugin could have derived
on its own.

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
- **Indentation means "continues the line above", nothing more.** Any width —
  one space, a tab, eight spaces — is re-emitted as a single
  `continuation_indent`. Depth is deliberately not carried: a continuation that
  would need a second level is, in practice, a new sentence.
- **A new sentence returns to column zero**, however deeply the author indented
  it. More precisely, the indent is dropped wherever the break would come back
  on its own: the plugin re-joins the two lines, asks its own break logic
  whether the newline reappears, and keeps the indent only when it does not.
  That is what stops a pinned break being lost on the next run — including
  inside a link label or other protected span, where no break is ever
  re-inserted.
- **Enabling it does not disturb documents this plugin has already formatted.**
  Its output carries no indentation, so there is nothing to pin and nothing
  moves. It is not a no-op in general: a document with indented continuation
  lines formats differently with it on, which is the point of the option. That
  is why it is off by default — turning it on is a decision about a particular
  repository's prose, not a safe global default.
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
  continuation marker therefore sits immediately *before* a break — ahead of a
  hard break's own backslash — and never at the start of the line the break
  opens, where it would hide the first character and silently suppress the
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

## License

MIT
