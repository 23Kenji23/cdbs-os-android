"""CDBS.OS — pedidos ao supervisor (outro processo) por arquivos, sem rede e sem shell.

O painel, o bot e a linha de comando NÃO executam nada: escrevem um pedido `data/system/control/requests/<id>.json`
com uma operação da lista permitida. O supervisor lê, executa a função correspondente (uma por vez) e grava o
resultado em `data/system/control/results/<id>.json`. Quem pediu espera o resultado REAL — nunca presume sucesso.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import time
from pathlib import Path

from sysutil import ensure_private_dir, read_json, scrub, write_json_private

OPERATIONS = {
    "server_restart": "reiniciar o servidor do CDBS",
    "server_start": "iniciar o servidor do CDBS (depois de parar por falhas seguidas)",
    "tunnel_start": "ligar o túnel Cloudflare",
    "tunnel_stop": "desligar o túnel Cloudflare",
    "tunnel_restart": "reiniciar o túnel Cloudflare",
    "update_apply": "instalar uma atualização preparada",
    "update_rollback": "voltar a uma versão anterior guardada",
    # 4.0 — servidores (aplicativos). args: {"name": <nome do servidor>}
    "app_start": "ligar um servidor",
    "app_stop": "desligar um servidor",
    "app_restart": "reiniciar um servidor",
    "app_deploy": "implantar o código novo de um servidor (com volta automática)",
    "app_rollback": "voltar um servidor para a versão anterior",
    "app_remove": "remover um servidor (os arquivos são guardados)",
}
APP_OPERATIONS = {op for op in OPERATIONS if op.startswith("app_")}
REQUEST_TTL = 300                       # pedido mais velho que 5 min é recusado (ex.: ficou na fila com o supervisor parado)
ID_RE = re.compile(r"^[0-9a-f]{16}$")


class ControlError(Exception):
    pass


def _dirs(data: Path) -> tuple[Path, Path]:
    base = data / "system" / "control"
    return base / "requests", base / "results"


def submit(data: Path, op: str, args: dict | None = None, *, by: str = "painel") -> str:
    if op not in OPERATIONS:
        raise ControlError("Operação não permitida.")
    requests, _ = _dirs(data)
    ensure_private_dir(requests)
    rid = secrets.token_hex(8)
    write_json_private(requests / f"{rid}.json", {"id": rid, "op": op, "args": args or {}, "by": scrub(by, 40), "created_at": time.time()})
    return rid


def result(data: Path, rid: str) -> dict | None:
    if not ID_RE.fullmatch(rid or ""):
        return None
    _, results = _dirs(data)
    found = read_json(results / f"{rid}.json")
    return found if isinstance(found, dict) and found.get("id") == rid else None


def wait(data: Path, rid: str, timeout: float, *, poll: float = 0.25, accept_running: bool = False) -> dict | None:
    """Espera o resultado final (ou, com accept_running, o primeiro sinal de que começou)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        found = result(data, rid)
        if found and (found.get("status") != "em_andamento" or accept_running):
            return found
        time.sleep(poll)
    return result(data, rid)


def pending(data: Path) -> list[dict]:
    """Lado do supervisor: pedidos na fila, mais antigos primeiro."""
    requests, _ = _dirs(data)
    if not requests.is_dir():
        return []
    items = []
    for path in requests.glob("*.json"):
        item = read_json(path)
        if isinstance(item, dict) and ID_RE.fullmatch(str(item.get("id", ""))) and path.stem == item["id"]:
            items.append(item)
        else:
            path.unlink(missing_ok=True)                       # lixo na pasta de pedidos: descartado
    return sorted(items, key=lambda i: i.get("created_at", 0))


def take(data: Path, rid: str) -> None:
    requests, _ = _dirs(data)
    (requests / f"{rid}.json").unlink(missing_ok=True)


def write_result(data: Path, rid: str, op: str, status: str, message: str, **detail: object) -> dict:
    _, results = _dirs(data)
    ensure_private_dir(results)
    payload = {"id": rid, "op": op, "status": status, "message": scrub(message, 400), "at": time.time(), **detail}
    write_json_private(results / f"{rid}.json", payload)
    # guarda só os 200 resultados mais recentes
    files = sorted(results.glob("*.json"), key=lambda p: p.stat().st_mtime)
    for old in files[:-200]:
        old.unlink(missing_ok=True)
    return payload


def is_expired(item: dict, now: float | None = None) -> bool:
    return (now or time.time()) - float(item.get("created_at") or 0) > REQUEST_TTL


def supervisor_alive(data: Path) -> dict | None:
    """Supervisor vivo = pid registrado existe, é mesmo o supervisor.py e o sinal de vida tem menos de 1 minuto."""
    info = read_json(data / "system" / "supervisor.json")
    if not isinstance(info, dict) or not info.get("pid") or info.get("stopped_at"):
        return None
    pid = int(info["pid"])
    try:
        os.kill(pid, 0)
    except (OSError, ValueError):
        return None
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as handle:
            if pid != os.getpid() and b"supervisor" not in handle.read():
                return None                                     # o pid foi reaproveitado por outro programa
    except OSError:
        pass                                                    # sem /proc (outro sistema): confia no pid + sinal de vida
    if time.time() - float(info.get("heartbeat_at") or 0) > 60:
        return None
    return info
