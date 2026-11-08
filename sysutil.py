"""CDBS.OS — utilidades compartilhadas, só com a biblioteca padrão.

* JSON gravado de forma atômica (arquivo temporário + troca) e privado (0600), em pasta privada (0700);
* leitura tolerante (arquivo ausente ou corrompido vira valor padrão, nunca exceção);
* mascaramento de dados sensíveis para registros, telas e mensagens: telefones, tokens, cookies e senhas.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import time
from pathlib import Path

try:                                    # Termux/Linux têm fcntl; em outros sistemas a trava entre processos vira só local
    import fcntl
except ImportError:                     # pragma: no cover - não existe no Android/Linux
    fcntl = None  # type: ignore[assignment]


def ensure_private_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass
    return path


def read_json(path: Path, default: object = None) -> object:
    """Lê JSON; ausente, ilegível ou corrompido devolve `default` (ou {})."""
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {} if default is None else default


def write_json_private(path: Path, data: object) -> None:
    """Grava JSON sem deixar arquivo pela metade: escreve ao lado, força no disco e troca de uma vez."""
    ensure_private_dir(path.parent)
    fd, temp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=1)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp, 0o600)
        os.replace(temp, path)
    except BaseException:
        try:
            os.unlink(temp)
        except OSError:
            pass
        raise


class FileLock:
    """Trava exclusiva entre processos (fcntl.flock). O sistema solta a trava sozinho se o processo morrer,
    então não existe "trava abandonada" que dure para sempre. `info` diz quem segura (para mensagens)."""

    def __init__(self, path: Path):
        self.path = path
        self._handle = None

    def acquire(self, timeout: float = 0.0, owner: str = "") -> bool:
        ensure_private_dir(self.path.parent)
        handle = open(self.path, "a+", encoding="utf-8")
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            try:
                if fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    handle.close()
                    return False
                time.sleep(0.05)
        handle.seek(0)
        handle.truncate()
        handle.write(json.dumps({"pid": os.getpid(), "owner": owner[:80], "since": int(time.time())}))
        handle.flush()
        self._handle = handle
        return True

    def release(self) -> None:
        handle, self._handle = self._handle, None
        if handle is None:
            return
        try:
            handle.seek(0)
            handle.truncate()
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()

    def info(self) -> dict:
        data = read_json(self.path)
        return data if isinstance(data, dict) else {}

    def __enter__(self) -> "FileLock":
        if not self.acquire(timeout=30):
            raise TimeoutError(f"trava ocupada: {self.path.name}")
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()


# --------------------------------------------------------------------------- mascaramento

_PHONE_RE = re.compile(r"(?<![\w.])\+?\d[\d\s().-]{8,18}\d(?![\w.])")
_LONG_SECRET_RE = re.compile(r"[A-Za-z0-9_\-+/=]{32,}")
_BEARER_RE = re.compile(r"(?i)\b(bearer|token|access_token|senha|password|secret|app_secret|verify_token)(\s*[:=]\s*|\s+)([^\s,;&\"']+)")
_COOKIE_RE = re.compile(r"(cdbs_session|cdbs_loja)=([A-Za-z0-9._-]+)")


def digits(value: object) -> str:
    return re.sub(r"\D", "", value) if isinstance(value, str) else ""


def mask_phone(value: object) -> str:
    """'+55 85 99999-8888' -> '+55•••••••8888' (país e 4 finais, o suficiente para reconhecer o número)."""
    only = digits(value)
    if len(only) < 8:
        return "•••"
    return f"+{only[:2]}{'•' * max(3, len(only) - 6)}{only[-4:]}"


def mask_secret(value: object) -> str:
    """Nunca mostra o segredo: só indica que existe e os 4 finais quando ele é longo o bastante."""
    if not isinstance(value, str) or not value:
        return ""
    return f"••••{value[-4:]}" if len(value) >= 16 else "••••"


def _mask_phone_match(match: re.Match) -> str:
    text = match.group(0)
    count = len(digits(text))
    return mask_phone(text) if 10 <= count <= 15 else text      # datas (2026-10-26) e números curtos ficam como estão


def _mask_long(match: re.Match) -> str:
    text = match.group(0)
    has_letter = any(c.isalpha() for c in text)
    has_digit = any(c.isdigit() for c in text)
    return "[segredo]" if has_letter and has_digit else text


def scrub(text: object, limit: int = 400) -> str:
    """Limpa um texto antes de ir para registro, tela ou WhatsApp: tokens, cookies, senhas e telefones mascarados."""
    if not isinstance(text, str):
        text = "" if text is None else str(text)
    text = text.replace("\r", " ").replace("\n", " ")
    text = _COOKIE_RE.sub(lambda m: f"{m.group(1)}=[sessão]", text)
    text = _BEARER_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}[oculto]", text)
    text = _LONG_SECRET_RE.sub(_mask_long, text)
    text = _PHONE_RE.sub(_mask_phone_match, text)
    return text[:limit]


def fmt_local(ts: object, with_seconds: bool = False) -> str:
    """Data/hora local do aparelho, no formato brasileiro."""
    if not isinstance(ts, (int, float)) or ts <= 0:
        return "—"
    return time.strftime("%d/%m/%Y %H:%M:%S" if with_seconds else "%d/%m/%Y %H:%M", time.localtime(ts))


def fmt_hour(ts: object) -> str:
    if not isinstance(ts, (int, float)) or ts <= 0:
        return "—"
    return time.strftime("%H:%M", time.localtime(ts))
