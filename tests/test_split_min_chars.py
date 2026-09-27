"""Separate sentence/clause thresholds, each falling back to ``min_chars``."""

from __future__ import annotations

import subprocess
import sys

import mdformat

from mdformat_sembr._sembr import insert_breaks

TEXT = "Hi. Apples, pears, and a much longer clause segment here."


def test_defaults_unchanged() -> None:
    # "Hi." is under the default threshold of 15, so no sentence break; the
    # clause break waits until "Hi. Apples, pears," reaches it.
    assert insert_breaks(TEXT, break_clauses=True) == (
        "Hi. Apples, pears,\nand a much longer clause segment here."
    )


def test_min_chars_applies_to_both() -> None:
    out = insert_breaks(TEXT, break_clauses=True, min_chars=1)
    assert out == (
        "Hi.\n"
        "Apples,\n"
        "pears,\n"
        "and a much longer clause segment here."
    )


def test_sentence_min_chars_overrides_sentences_only() -> None:
    out = insert_breaks(TEXT, break_clauses=True, sentence_min_chars=1)
    # Sentence break allowed; clause breaks keep the default threshold.
    assert out == "Hi.\nApples, pears, and a much longer clause segment here."


def test_clause_min_chars_overrides_clauses_only() -> None:
    out = insert_breaks(TEXT, break_clauses=True, min_chars=1, clause_min_chars=15)
    assert out == "Hi.\nApples, pears, and a much longer clause segment here."


def test_clause_min_chars_falls_back_to_custom_min_chars() -> None:
    out = insert_breaks(TEXT, break_clauses=True, min_chars=5, sentence_min_chars=1)
    # Clause threshold is 5: "Apples," (7) breaks; "pears," (6) breaks too.
    assert out == (
        "Hi.\n"
        "Apples,\n"
        "pears,\n"
        "and a much longer clause segment here."
    )


def test_shared_cut_point_uses_lower_threshold() -> None:
    # With "." also a clause char, the same point is both kinds of cut.
    out = insert_breaks(
        "Hi. There.",
        break_clauses=True,
        clause_chars=".",
        sentence_min_chars=1,
        clause_min_chars=50,
    )
    assert out == "Hi.\nThere."


def test_api_options() -> None:
    out = mdformat.text(
        TEXT + "\n",
        extensions={"sembr"},
        options={
            "plugin": {"sembr": {"break_clauses": True, "sentence_min_chars": 1}}
        },
    )
    assert out == "Hi.\nApples, pears, and a much longer clause segment here.\n"


def test_cli_options() -> None:
    result = subprocess.run(
        [
            sys.executable, "-m", "mdformat", "-",
            "--sembr-break-clauses",
            "--sembr-sentence-min-chars", "1",
            "--sembr-clause-min-chars", "5",
        ],
        input=TEXT + "\n",
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout == (
        "Hi.\n"
        "Apples,\n"
        "pears,\n"
        "and a much longer clause segment here.\n"
    )


def test_toml_config(tmp_path) -> None:
    cfg = tmp_path / ".mdformat.toml"
    cfg.write_text(
        "[plugin.sembr]\nbreak_clauses = true\nsentence_min_chars = 1\n",
        encoding="utf-8",
    )
    md = tmp_path / "doc.md"
    md.write_text(TEXT + "\n", encoding="utf-8")
    subprocess.run(
        [sys.executable, "-m", "mdformat", str(md)],
        capture_output=True,
        text=True,
        check=True,
        cwd=tmp_path,
    )
    assert md.read_text(encoding="utf-8") == (
        "Hi.\nApples, pears, and a much longer clause segment here.\n"
    )
