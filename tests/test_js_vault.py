"""Локальный PIN остаётся доступен после временной блокировки."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


def test_vault_pin_lockout_and_local_time():
    if shutil.which("node") is None:
        pytest.skip("node is not installed")
    script = Path(__file__).parent / "js" / "vault_lockout.mjs"
    result = subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
