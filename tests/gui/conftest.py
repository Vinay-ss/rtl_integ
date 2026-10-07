from __future__ import annotations

import shutil
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def copy_fixture(tmp_path: Path, name: str) -> Path:
    dst = tmp_path / name
    shutil.copytree(FIXTURES / name, dst, ignore=shutil.ignore_patterns("build", ".rtl_integ_gui"))
    return dst


@pytest.fixture
def proj_py(tmp_path) -> Path:
    """A writable copy of the python-template fixture project."""
    return copy_fixture(tmp_path, "proj_py")


@pytest.fixture
def proj_bt(tmp_path) -> Path:
    """proj_py written in the backtick syntax (` code, [* *] blocks, `var`)."""
    return copy_fixture(tmp_path, "proj_bt")


@pytest.fixture
def proj_pkg(tmp_path) -> Path:
    """Package types/enum values through header imports, implicit .name pins."""
    return copy_fixture(tmp_path, "proj_pkg")


@pytest.fixture
def proj_pl(tmp_path) -> Path:
    """Perl-template fixture project."""
    return copy_fixture(tmp_path, "proj_pl")


@pytest.fixture
def proj_auto(tmp_path) -> Path:
    """AUTO-style fixture (AUTOARG / AUTOWIRE / AUTOINST, an interface)."""
    return copy_fixture(tmp_path, "proj_auto")


@pytest.fixture
def proj_two(tmp_path) -> Path:
    """A wrapper module instantiated twice."""
    return copy_fixture(tmp_path, "proj_two")


@pytest.fixture
def proj_route(tmp_path) -> Path:
    """sample_env/route_demo as plain sources with //auto_route collection."""
    return copy_fixture(tmp_path, "proj_route")


@pytest.fixture
def slang():
    return pytest.importorskip("pyslang")
