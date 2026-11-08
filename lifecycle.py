"""CDBS.OS — sinal de vida e detecção de encerramento inesperado (Parte 1.6).

Enquanto roda, o processo regrava `data/system/<nome>-heartbeat.json` a cada minuto. Ao sair normalmente marca
`clean: true`. Se ao iniciar o arquivo anterior diz `clean: false`, o processo anterior NÃO terminou sozinho:
o Android encerrou o Termux, o celular reiniciou/desligou, faltou bateria ou o processo travou e foi morto.
Não dá para saber qual delas; dá para saber até quando ele estava vivo (o último sinal) — e isso é dito.
"""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Callable

from sysutil import fmt_local, read_json, write_json_private

BEAT_SECONDS = 60
PROBABLE_CAUSES = ("o Android encerrou o Termux (economia de bateria ou falta de memória), o celular reiniciou, "
                   "desligou ou ficou sem bateria, ou o processo foi fechado à força")


class Heartbeat:
    def __init__(self, path: Path, *, interval: float = BEAT_SECONDS, clock: Callable[[], float] = time.time):
        self.path, self.interval, self._clock = path, interval, clock
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._state: dict = {}

    def previous(self) -> dict | None:
        """Informação do processo anterior, se ele NÃO terminou normalmente; senão None."""
        data = read_json(self.path)
        if not isinstance(data, dict) or not data.get("started_at") or data.get("clean") is not False:
            return None
        last = int(data.get("beat_at") or data.get("started_at") or 0)
        return {"pid": data.get("pid"), "started_at": data.get("started_at"), "last_beat_at": last,
                "version": data.get("version", ""),
                "message": f"O CDBS anterior parou sem desligar normalmente. Último sinal de vida: {fmt_local(last)}. "
                           f"Causas prováveis: {PROBABLE_CAUSES}."}

    def start(self, **extra: object) -> None:
        now = int(self._clock())
        self._state = {"pid": os.getpid(), "started_at": now, "beat_at": now, "clean": False, **extra}
        self._write()
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name=f"cdbs-heartbeat-{self.path.stem}", daemon=True)
        self._thread.start()

    def update(self, **fields: object) -> None:
        self._state.update(fields)

    def _loop(self) -> None:
        while not self._stop.wait(self.interval):
            self._state["beat_at"] = int(self._clock())
            self._write()

    def _write(self) -> None:
        try:
            write_json_private(self.path, self._state)
        except OSError:
            pass

    def stop(self, clean: bool = True) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        if self._state:
            now = int(self._clock())
            self._state.update(beat_at=now, stopped_at=now, clean=bool(clean))
            self._write()
