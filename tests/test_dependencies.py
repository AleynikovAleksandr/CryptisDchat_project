"""pyproject.toml и requirements.txt описывают одни и те же зависимости.

Docker-образы ставят backend/requirements.txt, локальная разработка — requirements.txt или
`pip install -e ".[dev]"`; версии во всех местах должны совпадать.
"""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _pins(path: Path) -> set[str]:
    """Строки вида `name==версия` без комментариев и вложенных `-r`; имена в нижнем регистре."""
    pins = set()
    for line in path.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if line and not line.startswith("-"):
            pins.add(line.lower())
    return pins


def _names(reqs) -> set[str]:
    return {re.split(r"[\[=<>~!]", r, maxsplit=1)[0].strip().lower() for r in reqs}


def test_pyproject_matches_requirements():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    runtime = {d.lower() for d in project["dependencies"]}
    dev = {d.lower() for d in project["optional-dependencies"]["dev"]}

    assert runtime == _pins(ROOT / "backend" / "requirements.txt")
    assert dev == _pins(ROOT / "requirements.txt")
    # realm ставит свои диапазоны версий — все его пакеты есть и среди зафиксированных
    assert _names(_pins(ROOT / "realm" / "requirements.txt")) <= _names(runtime)
