"""CDBS.OS — histórico de auditoria (Parte 1.4).

Um arquivo de linhas JSON (`data/system/audit.jsonl`), só de acréscimo, com rotação por tamanho. Cada linha:
quando, categoria, ação, resultado, quem pediu (mascarado), alvo, detalhe curto e um identificador da ação.
Tudo passa por `sysutil.scrub` antes de ser gravado: tokens, senhas, cookies e telefones nunca entram por inteiro.

O supervisor (outro processo) escreve no mesmo arquivo; a gravação usa trava entre processos.
"""
from __future__ import annotations

import json
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Callable

from sysutil import FileLock, ensure_private_dir, scrub

CATEGORIES = {
    "servico": "Serviço",
    "servidores": "Servidores (apps)",
    "site": "Sites",
    "loja": "Lojas",
    "backup": "Backups",
    "restauracao": "Restaurações",
    "atualizacao": "Atualizações",
    "monitor": "Monitoramento",
    "tunel": "Túnel",
    "notificacao": "Notificações",
    "bot": "Bot WhatsApp",
    "seguranca": "Segurança",
    "sistema": "Sistema",
}
RESULTS = ("ok", "falha", "recusado", "aviso", "info")
MAX_BYTES = 512 * 1024
KEEP_FILES = 4                      # audit.jsonl + 3 antigos ≈ 2 MB no total


class AuditLog:
    def __init__(self, folder: Path, *, max_bytes: int = MAX_BYTES, keep: int = KEEP_FILES, clock: Callable[[], float] = time.time):
        self.folder = folder
        self.path = folder / "audit.jsonl"
        self.max_bytes, self.keep, self._clock = max_bytes, keep, clock
        self._lock = threading.Lock()
        self._listeners: list[Callable[[dict], None]] = []

    def add_listener(self, callback: Callable[[dict], None]) -> None:
        """Ex.: a central de notificações escuta eventos importantes. Um ouvinte com erro nunca impede o registro."""
        self._listeners.append(callback)

    def record(self, category: str, action: str, result: str = "ok", *, actor: str = "", target: str = "",
               detail: str = "", ref: str = "") -> dict:
        entry = {
            "id": secrets.token_hex(6),
            "at": int(self._clock()),
            "category": category if category in CATEGORIES else "sistema",
            "action": scrub(action, 60),
            "result": result if result in RESULTS else "info",
            "actor": scrub(actor, 60),
            "target": scrub(target, 80),
            "detail": scrub(detail, 300),
            "ref": scrub(ref, 40),
        }
        line = (json.dumps(entry, ensure_ascii=False) + "\n").encode("utf-8")
        try:
            with self._lock:
                ensure_private_dir(self.folder)
                lock = FileLock(self.folder / ".audit.lock")
                got = lock.acquire(timeout=5, owner="audit")
                try:
                    self._rotate_if_needed(len(line))
                    fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
                    try:
                        os.write(fd, line)
                    finally:
                        os.close(fd)
                finally:
                    if got:
                        lock.release()
        except OSError:
            pass                                    # o histórico nunca derruba a operação que ele registra
        for callback in list(self._listeners):
            try:
                callback(entry)
            except Exception:                       # noqa: BLE001
                pass
        return entry

    def _rotate_if_needed(self, incoming: int) -> None:
        try:
            size = self.path.stat().st_size
        except OSError:
            return
        if size + incoming <= self.max_bytes:
            return
        for index in range(self.keep - 1, 0, -1):
            older = self.folder / f"audit.{index}.jsonl"
            newer = self.folder / ("audit.jsonl" if index == 1 else f"audit.{index - 1}.jsonl")
            if newer.exists():
                os.replace(newer, older)

    def _files(self) -> list[Path]:
        files = [self.path] + [self.folder / f"audit.{i}.jsonl" for i in range(1, self.keep)]
        return [p for p in files if p.is_file()]

    def query(self, *, category: str | None = None, result: str | None = None, text: str | None = None,
              limit: int = 100, since: int | None = None) -> list[dict]:
        """Mais recentes primeiro. Filtros simples para o painel: categoria, resultado, texto e período."""
        limit = max(1, min(int(limit), 500))
        needle = (text or "").strip().lower()[:60]
        out: list[dict] = []
        for path in self._files():
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for raw in reversed(lines):
                try:
                    entry = json.loads(raw)
                except ValueError:
                    continue                        # linha cortada por um desligamento no meio: ignorada
                if not isinstance(entry, dict):
                    continue
                if category and entry.get("category") != category:
                    continue
                if result and entry.get("result") != result:
                    continue
                if since and int(entry.get("at", 0)) < since:
                    continue
                if needle and needle not in " ".join(str(entry.get(k, "")) for k in ("action", "target", "detail", "actor")).lower():
                    continue
                out.append(entry)
                if len(out) >= limit:
                    return out
        return out

    def last(self, category: str | None = None, action: str | None = None) -> dict | None:
        for entry in self.query(category=category, limit=500):
            if action is None or entry.get("action") == action:
                return entry
        return None
