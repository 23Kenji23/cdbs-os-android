"""CDBS.OS — pausar e retomar a publicação de um site, sem apagar nada.

Um site pausado continua com todos os arquivos no celular, mas o servidor dos sites responde 503 ("temporariamente
fora do ar") para as páginas, os produtos e as fotos dele. O painel do cliente continua funcionando.
É o que o painel e o bot chamam de "parar" e "iniciar/retomar" a publicação de uma posição: os sites são arquivos
estáticos servidos por um único servidor, então não existe um processo por site para ligar ou desligar.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

from sysutil import read_json, scrub, write_json_private


class Publication:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self._cache: tuple[float, dict] = (-1.0, {})

    def paused(self) -> dict[str, dict]:
        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            return {}
        cached_mtime, cached = self._cache
        if mtime != cached_mtime:
            data = read_json(self.path)
            paused = data.get("paused") if isinstance(data, dict) and isinstance(data.get("paused"), dict) else {}
            self._cache = (mtime, paused)
            return paused
        return cached

    def is_paused(self, slug: str) -> bool:
        return slug in self.paused()

    def pause(self, slug: str, by: str, reason: str = "") -> dict:
        with self._lock:
            data = read_json(self.path)
            data = data if isinstance(data, dict) else {}
            paused = data.get("paused") if isinstance(data.get("paused"), dict) else {}
            if slug in paused:
                return paused[slug]
            paused[slug] = {"at": int(time.time()), "by": scrub(by, 40), "reason": scrub(reason, 120)}
            data["paused"] = paused
            write_json_private(self.path, data)
            self._cache = (-1.0, {})
            return paused[slug]

    def resume(self, slug: str) -> bool:
        with self._lock:
            data = read_json(self.path)
            paused = data.get("paused") if isinstance(data, dict) and isinstance(data.get("paused"), dict) else {}
            if slug not in paused:
                return False
            paused.pop(slug)
            data["paused"] = paused
            write_json_private(self.path, data)
            self._cache = (-1.0, {})
            return True

    def forget(self, slug: str) -> None:
        """Site removido: some da lista de pausados (se for publicado de novo, volta no ar)."""
        self.resume(slug)
