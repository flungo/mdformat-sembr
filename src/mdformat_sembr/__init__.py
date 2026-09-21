"""mdformat-sembr: Semantic Line Breaks as CommonMark soft breaks.

The entry point ``mdformat.parser_extension`` -> ``sembr`` resolves to the
``_plugin`` attribute of this package (see ``pyproject.toml``). Importing it here
exposes the interface object as ``mdformat_sembr._plugin``.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

from mdformat_sembr import _plugin
from mdformat_sembr._sembr import insert_breaks

__all__ = ["_plugin", "insert_breaks", "__version__"]

try:
    #: Read from the installed distribution's metadata, which the build backend
    #: derives from the VCS tag. There is no second copy to drift out of step.
    __version__ = version("mdformat-sembr")
except PackageNotFoundError:  # pragma: no cover - running from an uninstalled tree
    __version__ = "0.0.0"
