"""mdformat-sembr: Semantic Line Breaks as CommonMark soft breaks.

The entry point ``mdformat.parser_extension`` -> ``sembr`` resolves to the
``_plugin`` attribute of this package (see ``pyproject.toml``). Importing it here
exposes the interface object as ``mdformat_sembr._plugin``.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError as _PackageNotFoundError
from importlib.metadata import version as _version

from mdformat_sembr import _plugin
from mdformat_sembr._sembr import insert_breaks

__all__ = ["_plugin", "insert_breaks"]

try:
    # pyproject.toml holds the only copy of the version; read it back from the
    # installed distribution instead of repeating it here (the two drifted).
    __version__ = _version("mdformat-sembr")
except _PackageNotFoundError:  # imported from a source tree, not installed
    __version__ = "0+unknown"
