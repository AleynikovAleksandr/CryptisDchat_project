"""Локальное хранилище зашифрованных блобов (том data_warehouses/uploads)."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

from app.interfaces.storage import IFileStorage


class LocalFileStorage(IFileStorage):
    def __init__(self, base_dir: str) -> None:
        self.base = Path(base_dir)

    def _path(self, key: str) -> Path:
        # ключ — UUID; раскладываем по подкаталогам, чтобы не держать миллионы файлов в одном
        safe = "".join(ch for ch in key if ch.isalnum() or ch == "-")
        return self.base / safe[:2] / safe[2:4] / f"{safe}.bin"

    async def save(self, key: str, data: bytes) -> str:
        path = self._path(key)

        def _write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(data)
            os.replace(tmp, path)

        await asyncio.to_thread(_write)
        return str(path.relative_to(self.base))

    async def read(self, path: str) -> bytes:
        full = self._resolve(path)
        return await asyncio.to_thread(full.read_bytes)

    def delete(self, path: str) -> None:
        full = self._resolve(path)
        try:
            full.unlink()
        except FileNotFoundError:
            pass

    def _resolve(self, path: str) -> Path:
        full = (self.base / path).resolve()
        if not str(full).startswith(str(self.base.resolve())):
            raise ValueError("path escapes storage root")
        return full
