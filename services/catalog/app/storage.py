"""Almacenamiento de fotos en disco. Las claves las genera el servidor (UUID), nunca el cliente."""

from __future__ import annotations

import os
import re
from pathlib import Path

KEY_RE = re.compile(r"^[0-9a-f]{32}-(1600|640)\.webp$")


class LocalStorage:
    def __init__(self, root: str | os.PathLike) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, key: str) -> Path:
        if not KEY_RE.match(key):                      # evita «../», rutas absolutas y nombres arbitrarios
            raise ValueError("clave de archivo inválida")
        return self.root / key

    def write(self, key: str, data: bytes) -> None:
        target = self.path(key)
        tmp = target.with_suffix(".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, target)                        # escritura atómica: nunca se sirve un archivo a medias

    def delete(self, key: str) -> None:
        self.path(key).unlink(missing_ok=True)

    def exists(self, key: str) -> bool:
        return KEY_RE.match(key) is not None and self.path(key).is_file()
