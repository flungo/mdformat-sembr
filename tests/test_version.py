"""The package must report exactly one version, and never hard-code it.

``pyproject.toml`` and ``__init__.py`` used to carry the number separately and
had already drifted apart (0.2.0 against 0.1.0). Both now derive from the VCS
tag, and these tests are what stops a second copy reappearing.
"""

from __future__ import annotations

from importlib.metadata import version
from pathlib import Path
from typing import Any

import pytest

import mdformat_sembr

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10, which this package still supports
    tomllib = None  # type: ignore[assignment]

PYPROJECT = Path(__file__).parent.parent / "pyproject.toml"

needs_tomllib = pytest.mark.skipif(
    tomllib is None, reason="reading pyproject.toml needs tomllib (3.11+)"
)


def _pyproject() -> dict[str, Any]:
    assert tomllib is not None
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


def test_version_comes_from_the_installed_distribution() -> None:
    assert mdformat_sembr.__version__ == version("mdformat-sembr")


def test_version_is_not_the_uninstalled_fallback() -> None:
    """The suite runs against an installed package, so the fallback is a bug."""
    assert mdformat_sembr.__version__ != "0.0.0"


@needs_tomllib
def test_pyproject_declares_the_version_dynamically() -> None:
    project = _pyproject()["project"]
    assert "version" in project.get("dynamic", [])
    assert "version" not in project, (
        "a static project.version reintroduces the drift this replaced"
    )


@needs_tomllib
def test_build_backend_supplies_the_version() -> None:
    config = _pyproject()
    assert config["tool"]["hatch"]["version"]["source"] == "uv-dynamic-versioning"
    requires = " ".join(config["build-system"]["requires"])
    assert "uv-dynamic-versioning" in requires, (
        "the version source plugin must be a build dependency"
    )


@needs_tomllib
def test_an_unresolvable_version_is_an_error_not_a_zero() -> None:
    """Silently publishing 0.0.0 would burn that version number on PyPI.

    A shallow checkout is the realistic way to get there: Dunamai refuses to
    resolve a tag in one, and a ``fallback-version`` would turn that refusal
    into a 0.0.0 release. An unpacked sdist needs no fallback — it carries the
    resolved version in ``PKG-INFO``.
    """
    settings = _pyproject()["tool"]["uv-dynamic-versioning"]
    assert settings.get("strict") is True
    assert "fallback-version" not in settings


def test_publish_workflow_checks_out_full_history() -> None:
    workflow = (
        PYPROJECT.parent / ".github" / "workflows" / "publish.yml"
    ).read_text(encoding="utf-8")
    assert "fetch-depth: 0" in workflow, (
        "a shallow checkout cannot resolve the tag the release is named after"
    )


def test_publish_workflow_verifies_the_version_against_the_tag() -> None:
    workflow = (
        PYPROJECT.parent / ".github" / "workflows" / "publish.yml"
    ).read_text(encoding="utf-8")
    assert "expects version" in workflow, (
        "the release must fail rather than publish a version that is not the tag"
    )
