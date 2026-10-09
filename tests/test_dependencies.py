"""pyproject.toml — единственный список зависимостей: Docker-образы и локальная разработка ставят его.

Все версии зафиксированы через ==, чтобы образы собирались воспроизводимо.
"""
from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]


def test_no_requirements_files():
    assert not [p for p in ROOT.rglob("requirements*.txt") if ".venv" not in p.parts]


def test_dependencies_are_pinned():
    extras = PROJECT["optional-dependencies"]
    reqs = PROJECT["dependencies"] + extras["backend"] + extras["dev"]
    self_refs = {r for r in reqs if r.startswith(PROJECT["name"] + "[")}
    assert self_refs == {"cryptisdchat[backend]"}
    assert all("==" in r for r in set(reqs) - self_refs)
