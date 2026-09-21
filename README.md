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

## Configuration

Configure via `[plugin.sembr]` in `.mdformat.toml`, or via CLI flags. CLI values merge
over TOML.

| Option          | Type        | Default   | Meaning                                                        |
| --------------- | ----------- | --------- | -------------------------------------------------------------- |
| `min_chars`     | int         | `15`      | Minimum length of the segment before a break is allowed.       |
| `abbreviations` | list[str]   | see below | Tokens after which no sentence break is inserted.              |
| `break_clauses` | bool        | `false`   | Enable clause-level breaks (SemBr "SHOULD"). Off by default.   |
| `clause_chars`  | str         | `",;:—"`  | Clause punctuation set (only used when `break_clauses` true).  |

CLI flags: `--sembr-min-chars`, `--sembr-abbreviations`, `--sembr-break-clauses`,
`--sembr-clause-chars`.

`.mdformat.toml` example:

```toml
[plugin.sembr]
min_chars = 20
break_clauses = true
```

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
`0.2.0.post3.dev0+a1b2c3d`. Building outside a git checkout is an error rather
than a `0.0.0` fallback, and the publish workflow both checks out full history
(a shallow clone cannot resolve the tag) and refuses to publish an artifact
whose version does not match the tag.

## License

MIT
