"""What counts as the start of the next sentence.

The boundary regex used to decide this with a lookahead for an ASCII capital
or digit, optionally behind one opening character. Two things defeated that:
the text is masked before the regex runs, so a sentence opening with a link or
a code span begins with a NUL placeholder and never matched (the backtick in
the lookahead could not occur any more); and a capital letter outside ASCII is
not in ``[A-Z]``. The decision now lives in ``_starts_sentence``.
"""

from __future__ import annotations

import mdformat

from mdformat_sembr._sembr import insert_breaks


def _br(text: str, **kw: object) -> str:
    return insert_breaks(text, min_chars=1, **kw)


# --- a masked construct can open a sentence --------------------------------

def test_sentence_starting_with_a_link() -> None:
    assert _br("First one. [A link](https://example.com) follows.") == (
        "First one.\n[A link](https://example.com) follows."
    )


def test_sentence_starting_with_an_image() -> None:
    assert _br("First one. ![Figure 1](fig.png) shows it.") == (
        "First one.\n![Figure 1](fig.png) shows it."
    )


def test_sentence_starting_with_a_reference_link() -> None:
    assert _br("First one. [Docs][docs] follow.") == "First one.\n[Docs][docs] follow."


def test_sentence_starting_with_a_code_span() -> None:
    # A code span routinely opens a sentence in technical prose, whatever its
    # case, so it counts as a sentence start unconditionally.
    assert _br("First one. `code` follows.") == "First one.\n`code` follows."
    assert _br("Set it. `git commit` records it.") == "Set it.\n`git commit` records it."


def test_link_with_a_lowercase_label_is_not_a_sentence_start() -> None:
    text = "See the note [in the guide](https://example.com) for details."
    assert "\n" not in _br(text)


def test_footnote_reference_is_not_a_sentence_start() -> None:
    assert "\n" not in _br("A claim. [^1] is the source, not a sentence.")


# --- opening markup ---------------------------------------------------------

def test_sentence_starting_with_strong_emphasis() -> None:
    # Two opening characters, not one.
    assert _br("First one. **Second** follows.") == "First one.\n**Second** follows."


def test_sentence_starting_with_a_curly_quote() -> None:
    assert _br("She paused. “Then go,” she said.") == (
        "She paused.\n“Then go,” she said."
    )


# --- capitals beyond ASCII --------------------------------------------------

def test_non_ascii_capital_starts_a_sentence() -> None:
    assert _br("Première phrase. Élément suivant.") == (
        "Première phrase.\nÉlément suivant."
    )


def test_non_ascii_lowercase_does_not() -> None:
    assert "\n" not in _br("Ein Satz mit z.B. ähnlichen Beispielen.")


def test_digit_still_starts_a_sentence() -> None:
    assert _br("First one. 42 follows.") == "First one.\n42 follows."


# --- through mdformat --------------------------------------------------------

def test_through_mdformat_with_default_threshold() -> None:
    src = "Read the introduction first. [The reference](https://example.com) comes after.\n"
    assert mdformat.text(src, extensions={"sembr"}) == (
        "Read the introduction first.\n[The reference](https://example.com) comes after.\n"
    )
