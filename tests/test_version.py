"""``__version__`` comes from the installed distribution, not a second copy."""

from __future__ import annotations

from importlib.metadata import version

import mdformat_sembr


def test_version_matches_installed_distribution() -> None:
    assert mdformat_sembr.__version__ == version("mdformat-sembr")
