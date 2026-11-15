# mdformat-sembr

> **Canonical home:** [codeberg.org/bugrasan/mdformat-sembr](https://codeberg.org/bugrasan/mdformat-sembr) — issues and contributions tracked there.
> The [GitHub mirror](https://github.com/bugrasan/mdformat-sembr) exists solely to enable PyPI Trusted Publishing and provenance attestation.

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

The `preserve_indented_breaks` option (see Configuration) puts two more hooks to work,
and without it neither does anything. markdown-it discards a continuation line's
indentation while tokenizing, so the `newline` and `escape` inline rules are wrapped to
record it as token metadata, and a postprocessor on the `softbreak`/`hardbreak` node
types passes it on as an internal marker line, which the paragraph postprocessor folds
away. Neither reaches the HTML renderer, so validation is unaffected. All of this lives
in `POSTPROCESSORS` rather than `RENDERERS` so that it chains with other plugins
instead of conflicting with them.

## Configuration

Configure via `[plugin.sembr]` in `.mdformat.toml`, or via CLI flags. CLI values merge
over TOML.

| Option                     | Type      | Default   | Meaning                                                              |
| -------------------------- | --------- | --------- | -------------------------------------------------------------------- |
| `min_chars`                | int       | `15`      | Minimum length of the segment before a break is allowed.             |
| `abbreviations`            | list[str] | see below | Tokens after which no sentence break is inserted.                    |
| `break_clauses`            | bool      | `false`   | Enable clause-level breaks (SemBr "SHOULD"). Off by default.         |
| `clause_chars`             | str       | `",;:—"`  | Clause punctuation set (only used when `break_clauses` true).        |
| `closing_punct`            | bool      | `false`   | Keep a closing quote or bracket after a terminator on the same line. |
| `continuation_indent`      | int       | `0`       | Indent continuation lines by N spaces (`2` when preserving breaks).  |
| `preserve_indented_breaks` | bool      | `false`   | Treat an indented line in the source as a break to keep.             |

CLI flags: `--sembr-min-chars`, `--sembr-abbreviations`, `--sembr-break-clauses`,
`--sembr-clause-chars`, `--sembr-closing-punct`, `--sembr-continuation-indent`,
`--sembr-preserve-indented-breaks`.

### Continuation indent

A clause break follows from its punctuation, so the plugin re-derives it on every run.
The breaks SemBr's *MAY* rules describe (6, 8, 10 and 11) follow from nothing: they
exist only because an author chose them. Indentation serves both, in two halves that
are opted into separately.

`continuation_indent` **writes** it — a line continuing the one above is indented,
after a clause break and a hard break alike — so with `break_clauses` on:

```markdown
They are endowed with reason and conscience,
  and should act towards one another in a spirit of brotherhood.
```

Delete that comma and the break goes with it, because nothing justifies it any more.

`preserve_indented_breaks` **reads** it — an indented line is a break to keep, as is
the line that returns to the margin straight after one, so the same paragraph survives
without the comma:

```markdown
They are endowed with reason and conscience
  and should act towards one another in a spirit of brotherhood.
```

That is the only way to keep a *MAY* break, since no punctuation implies one. The
indentation written is the plugin's own, however a line arrived: a kept line that starts
a sentence SemBr would break before goes back to the margin, and one that does not is
indented, the line that returned to the margin included.

A few breaks cannot be kept, and reflow as they would without preservation: one inside a
link or a code span, which the plugin never breaks within, and one before a line
starting with `~`, `:` or `%`, markers some plugins escape only in the first column. And
with `break_clauses` on as well, a clause break once indented reads back as kept, so
deleting its punctuation no longer removes it: for breaks that go with their
punctuation, use `continuation_indent` alone.

Preserving has nowhere to record a kept break but the indent below it, so it raises
`continuation_indent` to `2` unless you set it yourself — and setting that width to
`0` turns preservation off along with the indenting, with a warning, rather than
writing a document the next run would undo.

`.mdformat.toml` example:

```toml
[plugin.sembr]
min_chars = 20
break_clauses = true
preserve_indented_breaks = true
```

## License

MIT
