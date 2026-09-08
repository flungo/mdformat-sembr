"""A sentence that ends inside emphasis still ends.

"**A bold lead-in.** The rest." ends a sentence at the `**`. Unlike a closing
quote or bracket, whose placement relative to the full stop is a matter of
punctuation style (hence the opt-in ``closing_punct``), emphasis and
strikethrough closers are Markdown syntax and never part of the sentence, so
they are recognised unconditionally. The closer stays on the preceding line.
"""

from __future__ import annotations

import mdformat

from mdformat_sembr._sembr import insert_breaks


def _br(text: str, **kw: object) -> str:
    return insert_breaks(text, min_chars=1, **kw)


def test_strong_lead_in() -> None:
    assert _br("**A bold lead-in.** The rest of it.") == (
        "**A bold lead-in.**\nThe rest of it."
    )


def test_emphasis_lead_in() -> None:
    assert _br("*Emphasised.* Then plain text.") == "*Emphasised.*\nThen plain text."
    assert _br("_Emphasised._ Then plain text.") == "_Emphasised._\nThen plain text."


def test_strikethrough_lead_in() -> None:
    assert _br("~~Struck.~~ Then plain text.") == "~~Struck.~~\nThen plain text."


def test_closer_without_a_sentence_start_after_it_does_not_break() -> None:
    assert "\n" not in _br("**note.** and then lowercase continues.")


def test_abbreviation_inside_emphasis_does_not_break() -> None:
    assert "\n" not in _br("Use a tool, **e.g.** This one is fine.")


def test_quotes_and_brackets_still_need_closing_punct() -> None:
    text = 'He said "no thanks." Then he left.'
    assert "\n" not in _br(text)
    assert _br(text, closing_punct=True) == 'He said "no thanks."\nThen he left.'


def test_closing_punct_combines_with_emphasis_and_curly_quotes() -> None:
    assert _br("(**An aside.**) Then the point.", closing_punct=True) == (
        "(**An aside.**)\nThen the point."
    )
    assert _br("He said “no thanks.” Then he left.", closing_punct=True) == (
        "He said “no thanks.”\nThen he left."
    )


def test_list_item_lead_in_through_mdformat() -> None:
    src = "- **Pin the version.** Consumers pin the moving branch, not a tag.\n"
    assert mdformat.text(src, extensions={"sembr"}) == (
        "- **Pin the version.**\n  Consumers pin the moving branch, not a tag.\n"
    )
