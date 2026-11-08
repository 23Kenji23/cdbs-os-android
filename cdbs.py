#!/usr/bin/env python3
"""CDBS.OS: host simples de sites estáticos para Termux/Android.

Administração e sites escutam apenas em 127.0.0.1. Para publicar na internet,
configure um túnel HTTPS para a porta dos sites; nunca encaminhe a porta do painel.
"""
from __future__ import annotations

import email.utils
import hashlib
import http.client
import hmac
import json
import mimetypes
import os
import re
import secrets
import shutil
import socket
import stat
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path, PurePosixPath
from urllib.parse import parse_qs, unquote, urlsplit
from zipfile import BadZipFile, ZipFile

import backup
import catalog
import config
import versioninfo

ROOT = Path(__file__).resolve().parent

if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] == "doctor":
    # O diagnóstico roda ANTES de ler as portas: assim ele também funciona (e explica) quando o .env está errado.
    import doctor
    sys.exit(doctor.main(sys.argv[2:], ROOT))

import audit
import bot as bot_mod
import control
import lifecycle
import notify
import ops as ops_mod
import publication as publication_mod
import settings as settings_mod
import site_monitor
import apps as apps_mod
import updater as updater_mod
import whatsapp
from sysutil import ensure_private_dir, fmt_local, read_json, write_json_private
DATA = ROOT / "data"
SITES = DATA / "sites"
AUTH_FILE = DATA / "admin.json"
WEB = ROOT / "web"
MAX_ZIP = 60 * 1024 * 1024          # 4.0: 60 MB (era 15 MB) — o celular tem espaço de sobra
MAX_UNPACKED = 250 * 1024 * 1024    # 250 MB (era 50 MB)
MAX_FILES = 3000                    # (era 300)
MAX_DEPTH = 15
MAX_CONNECTIONS = 64      # o túnel abre várias conexões em paralelo (uma por arquivo da página)
SOCKET_TIMEOUT = 30       # silêncio máximo em uma conexão já estabelecida
HEADER_DEADLINE = 15      # prazo ABSOLUTO para o cliente terminar de enviar o pedido
PBKDF2_ROUNDS = 310_000
SESSION_SECONDS = 12 * 60 * 60
SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,30}[a-z0-9])?$")
BLOCKED_EXT = {".py", ".php", ".phtml", ".phar", ".cgi", ".pl", ".rb", ".sh", ".bash", ".zsh", ".exe", ".bat", ".cmd", ".ps1", ".dll", ".so", ".jar", ".war", ".class"}
# Lixo que macOS/Windows colocam em ZIPs: é ignorado em vez de recusar o site inteiro.
JUNK_NAMES = {".ds_store", "thumbs.db", "desktop.ini"}
JUNK_DIRS = {"__macosx"}
# O Termux nem sempre tem /etc/mime.types; sem isto um .js/.css/.woff2 pode sair com tipo errado
# e, com "nosniff", o navegador se recusa a executá-lo.
EXTRA_TYPES = {
    ".html": "text/html", ".css": "text/css", ".js": "text/javascript", ".mjs": "text/javascript",
    ".json": "application/json", ".webmanifest": "application/manifest+json", ".txt": "text/plain",
    ".svg": "image/svg+xml", ".webp": "image/webp", ".avif": "image/avif", ".ico": "image/x-icon",
    ".woff": "font/woff", ".woff2": "font/woff2", ".ttf": "font/ttf", ".otf": "font/otf",
    ".mp4": "video/mp4", ".webm": "video/webm", ".mp3": "audio/mpeg", ".wasm": "application/wasm",
}
for _ext, _type in EXTRA_TYPES.items():
    mimetypes.add_type(_type, _ext)
# Tipos de texto em que impomos UTF-8 quando o arquivo não declara outra codificação.
UTF8_TYPES = {"text/plain", "text/csv", "text/markdown", "application/json", "application/manifest+json"}
CHARSET_RE = re.compile(rb"charset\s*=\s*[\"']?\s*([A-Za-z0-9_.:-]+)", re.IGNORECASE)
SESSIONS: dict[str, float] = {}
LOGIN_LOCK = threading.Lock()
UPLOAD_LOCK = threading.Lock()
LOGIN_FAILURES = 0
LOGIN_BLOCKED_UNTIL = 0.0


def load_env() -> list[str]:
    """Lê um arquivo KEY=VALUE pequeno e deliberado (config.py); nunca executa sintaxe de shell."""
    return config.load_into_environ(ROOT)


def get_port(name: str, default: int) -> int:
    try:
        port = int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise RuntimeError(f"{name} deve ser um número.") from exc
    if not 1024 <= port <= 65535:
        raise RuntimeError(f"{name} precisa estar entre 1024 e 65535.")
    return port


load_env()
SITE_PORT = get_port("CDBS_SITE_PORT", 8088)
ADMIN_PORT = get_port("CDBS_ADMIN_PORT", 8089)
if SITE_PORT == ADMIN_PORT:
    raise RuntimeError("As portas do painel e dos sites devem ser diferentes.")
PUBLIC_BASE = os.getenv("CDBS_PUBLIC_BASE", "").strip().rstrip("/")
if PUBLIC_BASE:
    _base = urlsplit(PUBLIC_BASE)
    if _base.scheme != "https" or not _base.netloc or _base.username or _base.password or _base.query or _base.fragment:
        raise RuntimeError("CDBS_PUBLIC_BASE deve ser uma URL HTTPS válida.")
PANEL_PREFIX = "/_painel"  # o painel do cliente mora dentro do servidor de sites, no mesmo endereço do site
CLIENT_WEB = ROOT / "client"
VERSION_INFO = versioninfo.load(ROOT)
APP_VERSION = VERSION_INFO["version"]
STARTED_AT = int(time.time())


class CDBSError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def valid_slug(slug: str) -> bool:
    return bool(SLUG_RE.fullmatch(slug or ""))


def slugify(value: str) -> str:
    import unicodedata
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    result = re.sub(r"[^a-zA-Z0-9]+", "-", value.lower()).strip("-")
    result = re.sub(r"-+", "-", result)[:32].strip("-")
    return result or "site"


def _json_response(handler: BaseHTTPRequestHandler, data: object, status: int = 200) -> None:
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.end_headers()
    if handler.command != "HEAD":
        handler.wfile.write(body)


def _password_record(password: str) -> dict[str, str | int]:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ROUNDS)
    return {"salt": salt.hex(), "hash": digest.hex(), "rounds": PBKDF2_ROUNDS}


def _verify_password(password: str) -> bool:
    try:
        record = json.loads(AUTH_FILE.read_text(encoding="utf-8"))
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(record["salt"]), int(record["rounds"]))
        return hmac.compare_digest(digest.hex(), record["hash"])
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return False


_SESSION_LOCK = threading.Lock()


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("ascii", "ignore")).hexdigest()


def _sessions_file() -> Path:
    return DATA / "admin-sessions.json"


def _persisted_sessions() -> dict:
    data = read_json(_sessions_file())
    return {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, (int, float))} if isinstance(data, dict) else {}


def _save_persisted(sessions: dict) -> None:
    now = time.time()
    alive = dict(sorted(((k, v) for k, v in sessions.items() if v > now), key=lambda kv: kv[1])[-20:])   # no máximo 20 sessões abertas
    try:
        write_json_private(_sessions_file(), alive)
    except OSError:
        pass


def _new_session() -> str:
    """A sessão sobrevive a reinícios do servidor (atualização, supervisor): no disco fica só o HASH do token."""
    token = secrets.token_urlsafe(32)
    expiry = time.time() + SESSION_SECONDS
    SESSIONS[token] = expiry
    with _SESSION_LOCK:
        stored = _persisted_sessions()
        stored[_token_hash(token)] = expiry
        _save_persisted(stored)
    return token


def _end_session(token: str) -> None:
    SESSIONS.pop(token, None)
    with _SESSION_LOCK:
        stored = _persisted_sessions()
        if stored.pop(_token_hash(token), None) is not None:
            _save_persisted(stored)


def _is_authenticated(handler: BaseHTTPRequestHandler) -> bool:
    cookie = handler.headers.get("Cookie", "")
    match = re.search(r"(?:^|;\s*)cdbs_session=([A-Za-z0-9_-]+)", cookie)
    if not match:
        return False
    token = match.group(1)
    expiry = SESSIONS.get(token)
    if expiry is None:
        expiry = _persisted_sessions().get(_token_hash(token), 0)
        if expiry:
            SESSIONS[token] = expiry
    if not expiry or expiry < time.time():
        _end_session(token)
        return False
    return True


def _is_junk(path: PurePosixPath) -> bool:
    """Metadados de macOS/Windows (__MACOSX/, ._arquivo, .DS_Store, Thumbs.db)."""
    return (
        path.name.lower() in JUNK_NAMES
        or path.name.startswith("._")
        or any(part.lower() in JUNK_DIRS for part in path.parts[:-1])
    )


def _safe_zip_entries(archive: ZipFile) -> tuple[list[tuple[object, PurePosixPath]], int]:
    entries: list[tuple[object, PurePosixPath]] = []
    seen: set[str] = set()
    total = 0
    for info in archive.infolist():
        raw = info.filename
        if "\x00" in raw:
            raise CDBSError("O ZIP contém um caminho inválido.")
        # ZIPs criados no Windows (ex.: Compress-Archive do PowerShell 5.1) usam "\" como separador.
        # Normalizamos antes de validar, então "..\x" continua sendo recusado como "../x".
        raw = raw.replace("\\", "/")
        is_dir = raw.endswith("/")
        path = PurePosixPath(raw)
        if not path.parts:
            if is_dir:
                continue
            raise CDBSError("O ZIP contém um arquivo sem nome.")
        if path.is_absolute() or ":" in path.parts[0] or any(part in ("..", ".") for part in path.parts) or len(path.parts) > MAX_DEPTH:
            raise CDBSError("O ZIP contém um caminho inseguro.")
        mode = (info.external_attr >> 16) & 0xFFFF
        if stat.S_ISLNK(mode):
            raise CDBSError("Links simbólicos não são aceitos.")
        if is_dir:
            continue
        if _is_junk(path):
            continue
        if any(part.startswith(".") for part in path.parts):
            raise CDBSError("Arquivos ocultos, como .env, não são aceitos.")
        if path.suffix.lower() in BLOCKED_EXT:
            raise CDBSError("Um site aceita só arquivos estáticos. Para programas (Python/Node.js), use a página Servidores do painel.")
        normalized = path.as_posix()
        if normalized in seen:
            raise CDBSError("O ZIP contém arquivos duplicados.")
        seen.add(normalized)
        entries.append((info, path))
        total += info.file_size
        if len(entries) > MAX_FILES or total > MAX_UNPACKED:
            raise CDBSError("O site excede o limite de tamanho ou quantidade de arquivos.", 413)
    if not entries:
        raise CDBSError("O ZIP está vazio.")
    return entries, total


def install_site(slug: str, payload: bytes) -> None:
    with UPLOAD_LOCK:
        _install_site_locked(slug, payload)


def _install_site_locked(slug: str, payload: bytes) -> None:
    if not valid_slug(slug):
        raise CDBSError("Use um nome curto com letras sem acento, números e hífens.")
    if len(payload) > MAX_ZIP:
        raise CDBSError(f"O ZIP pode ter até {MAX_ZIP // (1024 * 1024)} MB.", 413)
    SITES.mkdir(parents=True, exist_ok=True)
    destination = SITES / slug
    if (DATA / "apps" / slug / "app.json").is_file():
        raise CDBSError(f"Já existe um SERVIDOR chamado {slug}. Sites e servidores dividem o endereço (/{slug}/): escolha outro nome.", 409)
    if destination.exists():
        raise CDBSError("Esse nome já está publicado. Remova-o antes de enviar uma versão nova.", 409)
    try:
        with ZipFile(BytesIO(payload)) as archive:
            entries, _ = _safe_zip_entries(archive)
            top = {path.parts[0] for _, path in entries}
            strip_root = None
            if not any(path.as_posix() == "index.html" for _, path in entries) and len(top) == 1:
                folder = next(iter(top))
                if any(path.as_posix() == f"{folder}/index.html" for _, path in entries):
                    strip_root = folder
            with tempfile.TemporaryDirectory(prefix=".cdbs-upload-", dir=SITES) as temporary:
                staging = Path(temporary) / "site"
                staging.mkdir()
                written = 0
                for info, path in entries:
                    parts = path.parts[1:] if strip_root and path.parts[0] == strip_root else path.parts
                    if not parts:
                        continue
                    target = staging.joinpath(*parts)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(info, "r") as source, target.open("xb") as output:
                        while chunk := source.read(64 * 1024):
                            written += len(chunk)
                            if written > MAX_UNPACKED:
                                raise CDBSError(f"O ZIP expandido passa de {MAX_UNPACKED // (1024 * 1024)} MB.", 413)
                            output.write(chunk)
                if not (staging / "index.html").is_file():
                    raise CDBSError("O ZIP precisa conter um index.html na pasta principal.")
                os.replace(staging, destination)
    except BadZipFile as exc:
        raise CDBSError("O arquivo não é um ZIP válido.") from exc
    except OSError as exc:
        raise CDBSError("Não foi possível salvar o site neste aparelho.", 500) from exc


# ------------------------------------------------------------------ sites removidos: guardados, recuperáveis (3.0)
# Remover um site NÃO apaga os arquivos: a pasta inteira vai para data/removidos/sites/<id>/site (privada, fora do
# servidor público e do túnel). Dá para recuperar pelo painel; apagar de vez é outra ação, com confirmação digitada.
# Nada aqui é apagado automaticamente.
REMOVED_ID_RE = re.compile(r"^[0-9a-f]{12}$")


def _removed_root() -> Path:
    return DATA / "removidos" / "sites"


def _tree_size(folder: Path) -> tuple[int, int]:
    size = files = 0
    for path in folder.rglob("*"):
        if path.is_file() and not path.is_symlink():
            size += path.stat().st_size
            files += 1
    return size, files


def move_site_to_removed(slug: str, by: str = "painel") -> dict:
    with UPLOAD_LOCK:
        folder = SITES / slug
        if not valid_slug(slug) or not folder.is_dir() or folder.is_symlink():
            raise CDBSError("O site não existe.", 404)
        ensure_private_dir(DATA / "removidos")
        root = ensure_private_dir(_removed_root())
        rid = secrets.token_hex(6)
        entry = root / rid
        entry.mkdir(mode=0o700)
        size, files = _tree_size(folder)
        info = {"id": rid, "slug": slug, "removed_at": int(time.time()), "bytes": size, "files": files, "by": by}
        write_json_private(entry / "info.json", info)            # primeiro o registro, depois a mudança (troca atômica da pasta)
        try:
            os.replace(folder, entry / "site")
        except OSError:
            shutil.rmtree(entry, ignore_errors=True)
            raise CDBSError("Não foi possível remover o site.", 500) from None
    return info


def removed_sites() -> list[dict]:
    root = _removed_root()
    items = []
    if root.is_dir():
        for entry in root.iterdir():
            if not entry.is_dir() or not REMOVED_ID_RE.fullmatch(entry.name):
                continue
            info = read_json(entry / "info.json")
            if not isinstance(info, dict) or info.get("id") != entry.name or not valid_slug(str(info.get("slug", ""))) or not (entry / "site").is_dir():
                continue                                          # registro sem pasta (queda no meio da remoção): o site nem saiu do lugar
            items.append({**{k: info.get(k) for k in ("id", "slug", "removed_at", "bytes", "files")},
                          "published_now": (SITES / info["slug"]).exists()})
    return sorted(items, key=lambda item: item.get("removed_at") or 0, reverse=True)


def _removed_entry(rid: str) -> tuple[Path, dict]:
    if not REMOVED_ID_RE.fullmatch(rid or ""):
        raise CDBSError("Cópia não encontrada.", 404)
    entry = _removed_root() / rid
    info = read_json(entry / "info.json")
    if not isinstance(info, dict) or info.get("id") != rid or not valid_slug(str(info.get("slug", ""))) or not (entry / "site").is_dir():
        raise CDBSError("Cópia não encontrada.", 404)
    return entry, info


def restore_removed_site(rid: str) -> dict:
    with UPLOAD_LOCK:
        entry, info = _removed_entry(rid)
        destination = SITES / info["slug"]
        if destination.exists():
            raise CDBSError(f"Já existe um site publicado com o nome {info['slug']}. Remova o atual primeiro (ele também fica "
                            "guardado aqui) e depois recupere este.", 409)
        SITES.mkdir(parents=True, exist_ok=True)
        os.replace(entry / "site", destination)
        shutil.rmtree(entry, ignore_errors=True)
    return info


def purge_removed_site(rid: str) -> dict:
    with UPLOAD_LOCK:
        entry, info = _removed_entry(rid)
        shutil.rmtree(entry)
    return info


class LocalOnlyServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 16

    def __init__(self, server_address, handler_class):
        self._connection_slots = threading.BoundedSemaphore(MAX_CONNECTIONS)
        super().__init__(server_address, handler_class)

    def get_request(self):
        request, address = super().get_request()
        request.settimeout(SOCKET_TIMEOUT)
        return request, address

    def process_request(self, request, client_address):
        if not self._connection_slots.acquire(blocking=False):
            # Lotado: avisa em vez de cortar em silêncio, para o túnel/navegador poder tentar de novo.
            try:
                request.settimeout(2)
                request.sendall(b"HTTP/1.0 503 Service Unavailable\r\nRetry-After: 2\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            except OSError:
                pass
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._connection_slots.release()
            raise

    def handle_error(self, request, client_address):
        # Cliente que desiste no meio do pedido (ou que derrubamos por demora) é normal:
        # não imprimir traceback no console do Termux.
        if isinstance(sys.exc_info()[1], (ConnectionError, TimeoutError)):
            return
        super().handle_error(request, client_address)

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._connection_slots.release()


def _content_type(target: Path) -> str:
    content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    if content_type in UTF8_TYPES:
        return f"{content_type}; charset=utf-8"
    if content_type == "text/html":
        # O cabeçalho HTTP vence o <meta charset>. Só impomos UTF-8 se a página não declarar nenhum;
        # assim sites antigos em ISO-8859-1 continuam corretos.
        try:
            with target.open("rb") as handle:
                head = handle.read(2048)
        except OSError:
            return content_type
        if not CHARSET_RE.search(head):
            return "text/html; charset=utf-8"
    return content_type


def _parse_range(spec: str, size: int) -> tuple[int, int] | bool | None:
    """Interpreta "bytes=<spec>". Devolve (início, fim), False (inatingível) ou None (ignorar o Range)."""
    if "," in spec or size == 0:
        return None
    match = re.fullmatch(r"(\d*)-(\d*)", spec.strip())
    if not match or not any(match.groups()):
        return None
    first, last = match.groups()
    if not first:  # "-N": os últimos N bytes
        count = int(last)
        return False if count == 0 else (max(size - count, 0), size - 1)
    start = int(first)
    if start >= size:
        return False
    end = int(last) if last else size - 1
    if end < start:
        return None
    return start, min(end, size - 1)


class DeadlineMixin:
    """Fecha conexões que demoram demais para enviar o pedido (ataque "slowloris").

    O timeout do socket só mede silêncio: quem manda um byte a cada 20 s o renova para sempre
    e ocupa todos os slots. Este prazo é absoluto e vale só até os cabeçalhos terminarem;
    depois disso volta a valer apenas o timeout normal (downloads lentos continuam permitidos).
    """

    _deadline: threading.Timer | None = None

    def handle(self) -> None:
        self._deadline = threading.Timer(HEADER_DEADLINE, self._abort_slow_request)
        self._deadline.daemon = True
        self._deadline.start()
        try:
            super().handle()
        finally:
            self._cancel_deadline()

    def parse_request(self) -> bool:
        try:
            return super().parse_request()
        finally:
            self._cancel_deadline()

    def _cancel_deadline(self) -> None:
        timer, self._deadline = self._deadline, None
        if timer is not None:
            timer.cancel()

    def _abort_slow_request(self) -> None:
        try:
            self.connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass


class AdminHandler(DeadlineMixin, BaseHTTPRequestHandler):
    server_version = "CDBS.OS"

    def parse_request(self) -> bool:
        """Defesa contra "DNS rebinding": o painel só atende pedidos cujo Host seja o endereço local dele."""
        if not super().parse_request():
            return False
        if self.headers.get("Host", "") not in {f"127.0.0.1:{ADMIN_PORT}", f"localhost:{ADMIN_PORT}"}:
            self.send_error(421, "Host não permitido")
            return False
        return True

    def log_message(self, fmt: str, *args: object) -> None:
        # Não registrar URLs, formulários nem outros dados enviados pelo usuário.
        return

    def _headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")

    def _json(self, data: object, status: int = 200, cookie: str | None = None) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._headers()
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _error(self, message: str, status: int = 400) -> None:
        self._json({"error": message}, status)

    def _origin_ok(self) -> bool:
        return self.headers.get("Origin", "") in {f"http://127.0.0.1:{ADMIN_PORT}", f"http://localhost:{ADMIN_PORT}"}

    def _body_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise CDBSError("Pedido inválido.")
        if length <= 0 or length > 16_384:
            raise CDBSError("Pedido inválido.", 413)
        try:
            data = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise CDBSError("Dados inválidos.")
        if not isinstance(data, dict):
            raise CDBSError("Dados inválidos.")
        return data

    def _declared_length(self) -> int:
        """Content-Length declarado; texto, negativo ou absurdo vira erro 400 (nunca 500)."""
        raw = self.headers.get("Content-Length", "0") or "0"
        if not raw.isdigit() or len(raw) > 12:
            raise CDBSError("Pedido inválido.")
        return int(raw)

    def _authorized(self) -> bool:
        if not _is_authenticated(self):
            self._error("Entre novamente no painel.", 401)
            return False
        return True

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if path in ("/", "/index.html"):
            self._serve_web("index.html", "text/html; charset=utf-8")
        elif path == "/app.css":
            self._serve_web("app.css", "text/css; charset=utf-8")
        elif path == "/app.js":
            self._serve_web("app.js", "text/javascript; charset=utf-8")
        elif path in ("/backups.js", "/ops.js", "/system.js", "/apps.js", "/nav.js"):
            self._serve_web(path[1:], "text/javascript; charset=utf-8")
        elif path.startswith("/api/backups/"):
            if self._authorized():
                self._backups("GET", path)
        elif path.split("/")[1:3] and path.split("/")[1] == "api" and path.split("/")[2] in SYSTEM_GET_ROUTES:
            if self._authorized():
                self._system("GET", path)
        elif path == "/api/session":
            self._json({"setup_required": not AUTH_FILE.exists(), "authenticated": _is_authenticated(self)})
        elif path == "/api/health":
            # Usado pelo supervisor e pelo diagnóstico (só local: o Host já foi conferido). Não revela dados.
            self._json({"ok": True, "version": APP_VERSION, "started_at": STARTED_AT, "pid": os.getpid()})
        elif path == "/api/sites":
            if not self._authorized():
                return
            sites = []
            if SITES.exists():
                for folder in sorted(SITES.iterdir(), key=lambda item: item.name):
                    if folder.is_dir() and valid_slug(folder.name) and (folder / "index.html").is_file():
                        path_url = f"/{folder.name}/"
                        sites.append({"slug": folder.name, "local_url": f"http://127.0.0.1:{SITE_PORT}{path_url}", "public_url": f"{PUBLIC_BASE}{path_url}" if PUBLIC_BASE else ""})
            self._json({"sites": sites, "public_configured": bool(PUBLIC_BASE)})
        elif path == "/api/stores":
            if not self._authorized():
                return
            stores = get_catalog().list_stores()
            for store in stores:
                store["site_published"] = (SITES / store["slug"] / "index.html").is_file()
            self._json({"stores": stores, "public_base": PUBLIC_BASE, "local_base": f"http://127.0.0.1:{SITE_PORT}"})
        else:
            self._error("Não encontrado.", 404)

    def _serve_web(self, name: str, content_type: str) -> None:
        try:
            body = (WEB / name).read_bytes()
        except OSError:
            self._error("Interface indisponível.", 500)
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self._headers()
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        if not self._origin_ok():
            self._error("Origem não permitida.", 403)
            return
        path = urlsplit(self.path).path
        if path == "/api/setup":
            self._setup()
        elif path == "/api/login":
            self._login()
        elif path == "/api/logout":
            if not self._authorized():
                return
            match = re.search(r"(?:^|;\s*)cdbs_session=([A-Za-z0-9_-]+)", self.headers.get("Cookie", ""))
            if match:
                _end_session(match.group(1))
            self._json({"ok": True}, cookie="cdbs_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0")
        elif path == "/api/sites":
            if not self._authorized():
                return
            self._upload_site()
        elif re.fullmatch(r"/api/stores(?:/[a-z0-9-]+(?:/password)?)?", path):
            if not self._authorized():
                return
            self._stores_post(path)
        elif path.startswith("/api/backups/"):
            if self._authorized():
                self._backups("POST", path)
        elif re.fullmatch(r"/api/sites/[a-z0-9-]+/(pause|resume)", path) or path.split("/")[2:3] and path.split("/")[2] in SYSTEM_POST_ROUTES:
            if self._authorized():
                self._system("POST", path)
        else:
            self._error("Não encontrado.", 404)

    def _backups(self, method: str, path: str) -> None:
        """Central de backups. Toda a lógica fica em backup.py; aqui só: sessão (já checada), limite de pedidos, download e envio."""
        if method != "GET":
            # Defesa em profundidade além do Origin: um formulário de OUTRO site não consegue enviar este cabeçalho
            # (o navegador exigiria permissão prévia, que este servidor nunca concede).
            if self.headers.get("X-CDBS-Request") != "1":
                self._error("Pedido não permitido.", 403)
                return
            if not BACKUP_GATE.allow():
                self._error("Muitos pedidos seguidos. Espere um minuto.", 429)
                return
        service = get_backup()
        try:
            download = re.fullmatch(r"/api/backups/([a-z0-9-]+)/([0-9a-f]{12})/download", path)
            upload = re.fullmatch(r"/api/backups/([a-z0-9-]+)/import", path)
            if method == "GET" and download:
                self._download_backup(service, download.group(1), download.group(2))
                return
            if method == "POST" and upload:
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    raise CDBSError("Pedido inválido.")
                secret = self.headers.get("X-CDBS-Senha")   # cabeçalho, não URL: URLs podem aparecer em registros
                self._json({"backup": service.import_archive(upload.group(1), self.rfile, length, secret)}, 201)
                return
            body: dict = {}
            if method != "GET" and self._declared_length() > 0:
                if not self.headers.get("Content-Type", "").lower().startswith("application/json"):
                    raise CDBSError("Tipo de conteúdo não permitido.", 415)
                body = self._body_json()
            status, data = service.handle(method, path, body)
            self._json(data, status)
        except backup.BackupError as exc:
            self._json({"error": str(exc), "code": exc.code, "next_action": exc.next_action, "operation": exc.record}, exc.status)
        except CDBSError as exc:
            self._error(str(exc), exc.status)
        except Exception:  # noqa: BLE001 — nunca devolver detalhes internos ao navegador
            self._error("Erro inesperado nos backups. Tente de novo.", 500)

    # ------------------------------------------------------------------ sistema: monitoramento, túnel, notificações, atualizações
    def _system(self, method: str, path: str) -> None:
        """Rotas da 3.0. Mesmas defesas da Central de backups: sessão (já checada), Origin (do_POST/do_DELETE),
        cabeçalho X-CDBS-Request, JSON nas escritas e limite de pedidos. Erros nunca devolvem detalhes internos."""
        if method != "GET":
            if self.headers.get("X-CDBS-Request") != "1":
                self._error("Pedido não permitido.", 403)
                return
            if not SYSTEM_GATE.allow():
                self._error("Muitos pedidos seguidos. Espere um minuto.", 429)
                return
        parts = path.strip("/").split("/")[1:]                   # sem o "api"
        try:
            if method == "POST" and parts[:2] == ["updates", "upload"]:
                self._json({"package": self._upload_update()}, 201)
                return
            if method == "POST" and len(parts) == 3 and parts[0] == "apps" and parts[2] == "upload":
                self._json({"staged": self._upload_app(parts[1])}, 201)
                return
            body: dict = {}
            if method != "GET" and self._declared_length() > 0:
                if not self.headers.get("Content-Type", "").lower().startswith("application/json"):
                    raise CDBSError("Tipo de conteúdo não permitido.", 415)
                body = self._body_json()
            status, data = self._system_route(method, parts, body, parse_qs(urlsplit(self.path).query))
            self._json(data, status)
        except (ops_mod.OpsError, settings_mod.SettingsError, site_monitor.MonitorError, updater_mod.UpdateError, apps_mod.AppError, CDBSError) as exc:
            self._json({"error": str(exc), "code": getattr(exc, "code", "")}, getattr(exc, "status", 400))
        except Exception:  # noqa: BLE001
            self._error("Erro inesperado. Tente de novo.", 500)

    def _system_route(self, method: str, parts: list[str], body: dict, query: dict) -> tuple[int, dict]:
        operations = get_ops()
        who = "painel"
        if method == "GET":
            if parts == ["overview"]:
                return 200, operations.overview()
            if parts == ["monitor"]:
                return 200, operations.slots()
            if parts == ["monitor", "history"]:
                return 200, {"rounds": operations.monitor.history(50)}
            if parts == ["events"]:
                pick = lambda name: (query.get(name) or [""])[0][:40] or None  # noqa: E731
                category, result = pick("category"), pick("result")
                if category and category not in audit.CATEGORIES:
                    raise CDBSError("Categoria inválida.")
                if result and result not in audit.RESULTS:
                    raise CDBSError("Resultado inválido.")
                try:
                    limit = int(pick("limit") or 100)
                except ValueError:
                    raise CDBSError("Limite inválido.") from None
                return 200, {"events": get_audit().query(category=category, result=result, text=pick("q"), limit=limit),
                             "categories": audit.CATEGORIES, "results": list(audit.RESULTS)}
            if parts == ["notifications"]:
                kind = (query.get("kind") or [""])[0] or None
                return 200, {"items": get_notifications().list(60, kind), "status": get_notifications().status()}
            if parts == ["notifications", "preview"]:
                return 200, operations.preview()
            if parts == ["settings"]:
                view = get_settings().view()
                base = operations.public_base()
                view["webhook_url"] = f"{base}{WEBHOOK_PATH}" if base else ""
                view["bot_status"] = get_bot().status()
                view["webhook_note"] = ("Endereço para cadastrar no painel da Meta (Webhooks → URL de retorno)." if base else
                                        "Sem link público agora: ligue o túnel (ou configure um endereço fixo) para ter o endereço do webhook.")
                return 200, view
            if parts == ["updates"]:
                return 200, {**get_updater().status(), "supervisor": bool(operations.supervisor())}
            if parts == ["tunnel"]:
                return 200, {"tunnel": operations.tunnel(), "supervisor": operations.supervisor() is not None}
            if parts == ["apps"]:
                return 200, operations.apps_view()
            if len(parts) == 3 and parts[0] == "apps" and parts[2] == "logs":
                try:
                    lines = int((query.get("lines") or ["200"])[0])
                except ValueError:
                    raise CDBSError("Número de linhas inválido.") from None
                return 200, {"name": parts[1], "lines": get_apps().log_tail(parts[1], lines)}
            if parts == ["storage"]:
                return 200, storage_report(0 if (query.get("fresh") or [""])[0] == "1" else 60.0)
            if parts == ["removed-sites"]:
                items = removed_sites()
                return 200, {"items": items, "total_bytes": sum(int(i.get("bytes") or 0) for i in items)}
            if len(parts) == 2 and parts[0] == "control":
                found = operations.request_result(parts[1])
                if found is None:
                    return 200, {"status": "aguardando", "message": "O supervisor ainda não pegou o pedido."}
                return 200, found
            raise CDBSError("Não encontrado.", 404)
        if method == "POST":
            if parts == ["monitor", "check"]:
                slot = body.get("slot")
                return 202, operations.check(slot if isinstance(slot, int) else None, who)
            if parts == ["monitor", "slots"]:
                slot, slug = body.get("slot"), body.get("slug")
                return 200, operations.assign(slot, slug if slug else None, who, body.get("expected_path"))
            if len(parts) == 3 and parts[0] == "sites" and parts[2] in ("pause", "resume"):
                slug = parts[1]
                if not valid_slug(slug):
                    raise CDBSError("Nome de site inválido.")
                if parts[2] == "pause":
                    if body.get("confirm") is not True:
                        raise CDBSError("Confirme para pausar: o site sai do ar para os visitantes.", 409)
                    reason = body.get("reason") if isinstance(body.get("reason"), str) else ""
                    return 200, {"ok": True, "paused": operations.pause(slug, who, reason[:120])}
                return 200, {"ok": True, "changed": operations.resume(slug, who)}
            if parts == ["apps"]:
                info = get_apps().create(body.get("name"), body.get("runtime"), body.get("entry"), health_path=body.get("health_path", ""),
                                         strip_prefix=body.get("strip_prefix", True), description=body.get("description", ""),
                                         taken=lambda n: (SITES / n).exists())
                _record("servidores", "app_criado", "ok", actor=who, target=info["name"], detail=f"{info['runtime']} · vaga {info['slot']} · porta {info['port']}")
                return 201, {"app": info}
            if len(parts) == 3 and parts[0] == "apps" and parts[2] == "config":
                info = get_apps().update(parts[1], {k: v for k, v in body.items() if k in ("runtime", "entry", "health_path", "strip_prefix",
                                                                                              "description", "max_body_mb", "timeout_s")})
                _record("servidores", "app_configurado", "ok", actor=who, target=parts[1], detail=", ".join(sorted(body))[:120])
                return 200, {"app": info, "note": "Vale a partir do próximo início: reinicie o servidor para aplicar."}
            if len(parts) == 3 and parts[0] == "apps" and parts[2] == "env":
                keys = get_apps().set_env(parts[1], body.get("set") or {}, body.get("remove") or [])
                _record("servidores", "app_variaveis", "ok", actor=who, target=parts[1], detail=f"{len(keys)} variável(is)")
                return 200, {"env_keys": keys, "note": "Vale a partir do próximo início: reinicie o servidor para aplicar."}
            if len(parts) == 3 and parts[0] == "apps" and parts[2] in ("start", "stop", "restart", "deploy", "rollback", "remove"):
                name, action = parts[1], parts[2]
                get_apps().get(name)
                if action == "remove" and body.get("confirm_text") != name:
                    raise CDBSError(f"Digite o nome do servidor ({name}) para confirmar a remoção.", 409)
                if action in ("stop", "restart", "rollback") and body.get("confirm") is not True:
                    raise CDBSError("Confirme: o endereço do servidor fica fora do ar por alguns segundos.", 409)
                return 202, {"request": operations.request(f"app_{action}", {"name": name}, who)}
            if len(parts) == 2 and parts[0] == "tunnel" and parts[1] in ("start", "stop", "restart"):
                if parts[1] != "start" and body.get("confirm") is not True:
                    raise CDBSError("Confirme: os links públicos param de abrir enquanto o túnel estiver desligado/reiniciando.", 409)
                return 202, {"request": operations.request(f"tunnel_{parts[1]}", {}, who)}
            if parts == ["system", "restart"]:
                if body.get("confirm") is not True:
                    raise CDBSError("Confirme: o painel e os sites ficam fora do ar por alguns segundos.", 409)
                return 202, {"request": operations.request("server_restart", {}, who)}
            if len(parts) == 2 and parts[0] == "settings":
                changes = {k: v for k, v in body.items() if k != "confirm"}
                updated = get_settings().update(parts[1], changes)
                _record("sistema", "configuracao_alterada", "ok", actor=who, target=parts[1], detail=", ".join(sorted(changes)))
                return 200, {"settings": get_settings().view(), "section": updated.get(parts[1])}
            if parts == ["secrets"]:
                name = body.get("name")
                if not isinstance(name, str) or name not in settings_mod.SECRETS:
                    raise CDBSError("Campo desconhecido.")
                get_settings().set_secret(name, body.get("value"))
                _record("seguranca", "credencial_salva", "ok", actor=who, target=name)
                return 200, {"secrets": get_settings().secrets_view()}
            if parts == ["secrets", "verify-token"]:
                token = get_settings().generate_verify_token()
                _record("seguranca", "credencial_salva", "ok", actor=who, target="whatsapp_verify_token", detail="token de verificação gerado")
                return 200, {"verify_token": token, "note": "Copie agora: ele não aparece de novo (dá para gerar outro)."}
            if parts == ["notifications", "test"]:
                if body.get("confirm") is not True:
                    raise CDBSError("Confirme o envio da mensagem de teste.", 409)
                preview = operations.preview()
                channel = body.get("channel", "whatsapp")
                if channel not in ("whatsapp", "email"):
                    raise CDBSError("Canal inválido.")
                event = get_notifications().send_test(preview["text"], preview["params"], only=channel)
                _record("notificacao", "teste_pedido", "ok", actor=who, detail=event.get("note") or event["status"])
                return 202, {"event": {k: event[k] for k in ("id", "status", "note")}}
            if len(parts) == 3 and parts[0] == "updates" and parts[2] == "install":
                if body.get("confirm_text") != "INSTALAR":
                    raise CDBSError("Digite INSTALAR para confirmar.", 409)
                report = get_updater().get_staged(parts[1])
                if not report.get("ok"):
                    raise CDBSError("Este pacote não passou na validação.", 409)
                return 202, {"request": operations.request("update_apply", {"staged_id": parts[1], "allow_same_or_older": body.get("allow_same_or_older") is True}, who)}
            if len(parts) == 3 and parts[0] == "removed-sites" and parts[2] == "restore":
                info = restore_removed_site(parts[1])
                _record("site", "site_recuperado", "ok", actor=who, target=info["slug"], detail="site removido voltou ao ar", ref=parts[1])
                slot = _safe(lambda: operations.on_published(info["slug"]))
                return 200, {"ok": True, "slug": info["slug"], "slot": slot}
            if parts == ["updates", "rollback"]:
                if body.get("confirm_text") != "VOLTAR":
                    raise CDBSError("Digite VOLTAR para confirmar.", 409)
                snap = body.get("snapshot")
                if not isinstance(snap, str) or snap not in {s["id"] for s in get_updater().snapshots()}:
                    raise CDBSError("Cópia não encontrada.", 404)
                return 202, {"request": operations.request("update_rollback", {"snapshot_id": snap}, who)}
            raise CDBSError("Não encontrado.", 404)
        if method == "DELETE":
            if len(parts) == 2 and parts[0] == "secrets":
                get_settings().clear_secret(parts[1])
                _record("seguranca", "credencial_apagada", "ok", actor=who, target=parts[1])
                return 200, {"secrets": get_settings().secrets_view()}
            if len(parts) == 2 and parts[0] == "updates":
                get_updater().discard(parts[1])
                return 200, {"ok": True}
            if len(parts) == 2 and parts[0] == "removed-sites":
                if body.get("confirm_text") != "APAGAR":
                    raise CDBSError("Digite APAGAR para confirmar: a cópia guardada é apagada para sempre.", 409)
                info = purge_removed_site(parts[1])
                _record("site", "site_apagado", "ok", actor=who, target=info["slug"], detail="cópia guardada apagada de vez pelo administrador", ref=parts[1])
                return 200, {"ok": True}
        raise CDBSError("Não encontrado.", 404)

    def _upload_app(self, name: str) -> dict:
        content_type = self.headers.get("Content-Type", "").lower()
        if not (content_type.startswith("application/zip") or content_type.startswith("application/octet-stream") or content_type.startswith("application/x-zip")):
            raise CDBSError("Envie o arquivo .zip com o código do servidor.", 415)
        info = get_apps().stage_zip(name, self.rfile, self._declared_length())
        _record("servidores", "app_codigo_recebido", "ok", actor="painel", target=name, detail=f"{info['files']} arquivos, {info['bytes']} bytes")
        return info

    def _upload_update(self) -> dict:
        content_type = self.headers.get("Content-Type", "").lower()
        if not (content_type.startswith("application/zip") or content_type.startswith("application/octet-stream") or content_type.startswith("application/x-zip")):
            raise CDBSError("Envie o arquivo .zip do pacote.", 415)
        report = get_updater().stage_upload(self.rfile, self._declared_length())
        _record("atualizacao", "pacote_recebido", "ok" if report["ok"] else "recusado", actor="painel",
                detail=f"versão {report.get('version') or '?'}: " + ("aprovado na validação" if report["ok"] else "; ".join(report["problems"])[:200]))
        return report

    def _download_backup(self, service: backup.BackupService, slug: str, rid: str) -> None:
        path, size, filename = service.prepare_export(slug, rid)   # valida de novo; só sai backup íntegro
        sent = 0
        try:
            with path.open("rb") as handle:
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream" if filename.endswith(".cdbk") else "application/zip")
                self.send_header("Content-Length", str(size))
                self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
                self._headers()
                self.end_headers()
                while chunk := handle.read(256 * 1024):
                    self.wfile.write(chunk)
                    sent += len(chunk)
            service.record_export(slug, rid, sent == size, sent)
        except (BrokenPipeError, ConnectionResetError, socket.timeout, OSError):
            service.record_export(slug, rid, False, sent)

    def _stores_post(self, path: str) -> None:
        """Criar catálogo (POST /api/stores), editar (/api/stores/<nome>) ou gerar nova senha (/api/stores/<nome>/password)."""
        store_catalog = get_catalog()
        try:
            data = self._body_json()
            if path == "/api/stores":
                slug = data.get("slug")
                slug = slug.strip().lower() if isinstance(slug, str) else ""
                password = store_catalog.create_store(slug, data.get("name"), data.get("whatsapp"))
                _record("loja", "loja_criada", "ok", actor="painel", target=slug)
                self._json({"ok": True, "slug": slug, "password": password, "public_base": PUBLIC_BASE}, 201)
                return
            slug = path.split("/")[3]
            if path.endswith("/password"):
                was_pending = store_catalog.is_pending(slug)
                new_password = store_catalog.reset_password(slug)
                _record("loja", "loja_ativada" if was_pending else "senha_loja_trocada", "ok", actor="painel", target=slug)
                self._json({"ok": True, "slug": slug, "password": new_password, "public_base": PUBLIC_BASE})
            else:
                store_catalog.update_store(slug, data)
                _record("loja", "loja_editada", "ok", actor="painel", target=slug)
                self._json({"ok": True})
        except (catalog.CatalogError, CDBSError) as exc:
            self._error(str(exc), exc.status)

    def _setup(self) -> None:
        if AUTH_FILE.exists():
            self._error("O painel já foi configurado.", 409)
            return
        try:
            data = self._body_json()
            password = data.get("password", "")
            if not isinstance(password, str) or len(password) < 12 or len(password) > 256:
                raise CDBSError("Crie uma senha com pelo menos 12 caracteres.")
            DATA.mkdir(parents=True, exist_ok=True)
            temp = AUTH_FILE.with_suffix(".tmp")
            temp.write_text(json.dumps(_password_record(password)), encoding="utf-8")
            os.chmod(temp, 0o600)
            os.replace(temp, AUTH_FILE)
            _record("seguranca", "senha_painel_criada", "ok", actor="painel")
            self._set_session()
        except CDBSError as exc:
            self._error(str(exc), exc.status)

    def _login(self) -> None:
        global LOGIN_FAILURES, LOGIN_BLOCKED_UNTIL
        if not AUTH_FILE.exists():
            self._error("Configure uma senha primeiro.", 409)
            return
        # A tentativa é contada ANTES de verificar a senha (a verificação é lenta de propósito):
        # assim, pedidos simultâneos não conseguem ultrapassar o limite de 5.
        with LOGIN_LOCK:
            now = time.time()
            if now < LOGIN_BLOCKED_UNTIL:
                self._error("Muitas tentativas. Tente novamente em cinco minutos.", 429)
                return
            if LOGIN_BLOCKED_UNTIL:  # o bloqueio anterior já expirou
                LOGIN_BLOCKED_UNTIL = 0.0
                LOGIN_FAILURES = 0
            LOGIN_FAILURES += 1
            if LOGIN_FAILURES >= 5:
                LOGIN_BLOCKED_UNTIL = now + 300
        try:
            data = self._body_json()
            password = data.get("password", "")
            valid = isinstance(password, str) and _verify_password(password)
        except CDBSError as exc:
            self._error(str(exc), exc.status)
            return
        if not valid:
            _record("seguranca", "login_painel", "recusado", actor="painel", detail="senha incorreta")
            self._error("Senha incorreta.", 401)
            return
        with LOGIN_LOCK:
            LOGIN_FAILURES = 0
            LOGIN_BLOCKED_UNTIL = 0
        _record("seguranca", "login_painel", "ok", actor="painel")
        self._set_session()

    def _set_session(self) -> None:
        token = _new_session()
        self._json({"ok": True}, cookie=f"cdbs_session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age={SESSION_SECONDS}")

    def _upload_site(self) -> None:
        query = parse_qs(urlsplit(self.path).query)
        slug = query.get("slug", [""])[0]
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > MAX_ZIP:
                raise CDBSError(f"Envie um ZIP de até {MAX_ZIP // (1024 * 1024)} MB.", 413)
            payload = self.rfile.read(length)
            if len(payload) != length:
                raise CDBSError("Upload incompleto.")
            install_site(slug, payload)
            _record("site", "site_publicado", "ok", actor="painel", target=slug, detail=f"{len(payload)} bytes")
            slot = _safe(lambda: get_ops().on_published(slug))
            self._json({"ok": True, "slug": slug, "slot": slot, "local_url": f"http://127.0.0.1:{SITE_PORT}/{slug}/", "public_url": f"{PUBLIC_BASE}/{slug}/" if PUBLIC_BASE else ""}, 201)
        except CDBSError as exc:
            _record("site", "site_publicado", "falha", actor="painel", target=slug if valid_slug(slug) else "", detail=str(exc))
            self._error(str(exc), exc.status)

    def do_DELETE(self) -> None:
        if not self._origin_ok():
            self._error("Origem não permitida.", 403)
            return
        if not self._authorized():
            return
        path = unquote(urlsplit(self.path).path)
        if re.fullmatch(r"/api/(secrets/[a-z_]+|updates/[0-9a-f]{12}|removed-sites/[0-9a-f]{12})", path):
            self._system("DELETE", path)
            return
        store_match = re.fullmatch(r"/api/stores/([a-z0-9-]+)", path)
        if store_match:
            try:
                get_catalog().delete_store(store_match.group(1))
            except catalog.CatalogError as exc:
                self._error(str(exc), exc.status)
                return
            _record("loja", "loja_removida", "ok", actor="painel", target=store_match.group(1))
            self._json({"ok": True})
            return
        match = re.fullmatch(r"/api/sites/([a-z0-9-]+)", path)
        if not match or not valid_slug(match.group(1)):
            self._error("Não encontrado.", 404)
            return
        folder = SITES / match.group(1)
        if not folder.is_dir():
            self._error("O site não existe.", 404)
            return
        try:
            if folder.is_symlink():
                folder.unlink()                                   # link criado à mão fora do painel: não há arquivos nossos a guardar
                kept = None
            else:
                kept = move_site_to_removed(match.group(1))
        except (OSError, CDBSError):
            _record("site", "site_removido", "falha", actor="painel", target=match.group(1), detail="não foi possível tirar a pasta do ar")
            self._error("Não foi possível remover o site.", 500)
            return
        _record("site", "site_removido", "ok", actor="painel", target=match.group(1),
                detail="arquivos guardados em Sites removidos (dá para recuperar)" if kept else "link removido", ref=kept["id"] if kept else "")
        _safe(lambda: get_ops().on_removed(match.group(1)))
        self._json({"ok": True, "kept": bool(kept), "removed_id": kept["id"] if kept else None})


def get_catalog() -> catalog.Catalog:
    return catalog.Catalog(DATA / "catalogs", DATA / "secret.key")


BACKUP_GATE = backup.RateGate(90, 60)  # limite de pedidos que alteram dados na Central de backups
SYSTEM_GATE = backup.RateGate(60, 60)  # idem para monitoramento, túnel, notificações e atualizações
WEBHOOK_GATE = backup.RateGate(300, 60)  # eventos do WhatsApp por minuto (bem acima do uso normal)
WEBHOOK_PATH = "/_whatsapp/webhook"
WEBHOOK_MAX = 512 * 1024
SYSTEM_GET_ROUTES = {"overview", "monitor", "events", "notifications", "settings", "updates", "tunnel", "control", "removed-sites", "apps", "storage"}
SYSTEM_POST_ROUTES = {"monitor", "tunnel", "system", "settings", "secrets", "notifications", "updates", "removed-sites", "apps"}
_BACKUP_SERVICES: dict[tuple, "backup.BackupService"] = {}
_BACKUP_LOCK = threading.Lock()


def get_backup() -> backup.BackupService:
    """Uma instância por pasta de dados (guarda operações em andamento e confirmações de restauração)."""
    key = (str(DATA), str(SITES))
    with _BACKUP_LOCK:
        service = _BACKUP_SERVICES.get(key)
        if service is None:
            service = _BACKUP_SERVICES[key] = backup.BackupService(DATA, SITES, DATA / "catalogs")
            service.observer = _backup_observer
        return service


_REGISTRY_LOCK = threading.RLock()
_SERVICES: dict[tuple[str, str], object] = {}


def _service(name: str, factory):
    """Uma instância de cada serviço por pasta de dados (os testes trocam a pasta e ganham serviços novos)."""
    key = (name, str(DATA))
    with _REGISTRY_LOCK:
        found = _SERVICES.get(key)
        if found is None:
            found = _SERVICES[key] = factory()
        return found


def get_apps() -> apps_mod.AppStore:
    return _service("apps", lambda: apps_mod.AppStore(DATA))


_STORAGE_CACHE: dict = {}
_STORAGE_LOCK = threading.Lock()


def storage_report(max_age: float = 60.0) -> dict:
    """Espaço do celular e quanto cada parte do CDBS ocupa. Somar pastas grandes demora: guarda por 1 minuto."""
    with _STORAGE_LOCK:
        cached = _STORAGE_CACHE.get(str(DATA))
        if cached and time.time() - cached["at"] < max_age:
            return cached["report"]
        try:
            disk = shutil.disk_usage(DATA if DATA.exists() else ROOT)
            device = {"total": disk.total, "used": disk.used, "free": disk.free}
        except OSError:
            device = {"total": None, "used": None, "free": None}
        parts = {}
        for key, folder in (("sites", SITES), ("servidores", DATA / "apps"), ("backups", DATA / "backups"), ("lojas", DATA / "catalogs"),
                            ("removidos", DATA / "removidos"), ("atualizacoes", DATA / "updates"), ("sistema", DATA / "system"),
                            ("monitoramento", DATA / "monitoring"), ("notificacoes", DATA / "notifications")):
            parts[key] = apps_mod.tree_size(folder)[0]
        sites = {}
        if SITES.is_dir():
            for folder in SITES.iterdir():
                if folder.is_dir() and valid_slug(folder.name):
                    sites[folder.name] = apps_mod.tree_size(folder)[0]
        report = {"device": device, "parts": parts, "cdbs_total": sum(parts.values()), "sites": sites,
                  "apps": get_apps().disk(), "measured_at": int(time.time())}
        _STORAGE_CACHE[str(DATA)] = {"at": time.time(), "report": report}
        return report


def get_audit() -> audit.AuditLog:
    """Histórico de auditoria da pasta de dados atual (data/system/audit.jsonl)."""
    return _service("audit", lambda: audit.AuditLog(DATA / "system"))


def get_settings() -> settings_mod.Settings:
    return _service("settings", lambda: settings_mod.Settings(DATA / "system"))


def get_publication() -> publication_mod.Publication:
    return _service("publication", lambda: publication_mod.Publication(DATA / "system" / "publication.json"))


def _whatsapp_sender() -> whatsapp.CloudAPI:
    config_ = get_settings()
    wa = config_.get()["whatsapp"]
    return whatsapp.CloudAPI(config_.secret("whatsapp_phone_number_id"), config_.secret("whatsapp_token"), api_version=wa["api_version"],
                             base=os.environ.get("CDBS_WHATSAPP_API_BASE", "").strip() or whatsapp.DEFAULT_BASE)


def _email_sender():
    import emailer                                                          # noqa: PLC0415
    config_ = get_settings()
    mail = config_.get()["email"]
    return emailer.SMTPSender(mail["smtp_host"], mail["smtp_port"], mail["smtp_security"], mail["smtp_user"],
                              config_.secret("email_password"), mail["from_address"] or mail["smtp_user"])


def get_notifications() -> notify.NotificationCenter:
    def make() -> notify.NotificationCenter:
        center = notify.NotificationCenter(DATA / "notifications", get_settings(), sender_factory=_whatsapp_sender, audit=get_audit(),
                                           mailer_factory=lambda: _email_sender(),
                                           disk_path=DATA, overdue=lambda: int(get_backup().reminder_status().get("overdue", 0) or 0))
        get_audit().add_listener(center.on_audit)
        return center
    return _service("notifications", make)


def get_ops() -> ops_mod.Ops:
    def make() -> ops_mod.Ops:
        def monitor_factory(operations: ops_mod.Ops) -> site_monitor.Monitor:
            return site_monitor.Monitor(
                DATA, SITES, site_port=lambda: SITE_PORT, publication=get_publication(),
                maintenance=lambda slug: get_backup().in_maintenance(slug), settings=get_settings(),
                public_base=operations.public_base, tunnel_state=operations.tunnel, audit=get_audit(),
                on_round=lambda report, changes, source: get_notifications().on_round(report, changes, source, next_at=operations.monitor.next_at))
        return ops_mod.Ops(root=ROOT, data=DATA, sites=SITES, site_port=lambda: SITE_PORT, admin_port=lambda: ADMIN_PORT,
                           fixed_public_base=lambda: PUBLIC_BASE, audit=get_audit(), settings=get_settings(), publication=get_publication(),
                           monitor_factory=monitor_factory, notifications=get_notifications(), version=lambda: APP_VERSION,
                           started_at=lambda: STARTED_AT, maintenance=lambda slug: get_backup().in_maintenance(slug))
    return _service("ops", make)


def get_bot() -> bot_mod.Bot:
    return _service("bot", lambda: bot_mod.Bot(DATA / "system", settings=get_settings(), ops=get_ops(), notifications=get_notifications(),
                                               audit=get_audit(), sender_factory=_whatsapp_sender))


_REFUSED_AT = [0.0]


def _webhook_refused(reason: str) -> None:
    """Recusa no webhook vai para o histórico no máximo 1 vez por minuto (um ataque não enche o disco)."""
    now = time.time()
    if now - _REFUSED_AT[0] >= 60:
        _REFUSED_AT[0] = now
        _record("bot", "webhook_recusado", "recusado", actor="internet", detail=reason)


def get_updater() -> updater_mod.Updater:
    return _service("updater", lambda: updater_mod.Updater(ROOT, DATA))


def _safe(func):
    """Efeito secundário (monitoramento, notificação) nunca desfaz nem derruba a operação principal."""
    try:
        return func()
    except Exception:  # noqa: BLE001
        return None


def _record(category: str, action: str, result: str = "ok", **fields: str) -> None:
    try:
        get_audit().record(category, action, result, **fields)
    except Exception:  # noqa: BLE001 — o histórico nunca derruba a operação
        pass


_BACKUP_EVENT_NAMES = {
    ("backup", "CONCLUIDO"): ("backup", "backup_concluido", "ok"),
    ("backup", "FALHOU"): ("backup", "backup_falhou", "falha"),
    ("backup", "INVALIDO"): ("backup", "backup_invalido", "falha"),
    ("backup", "EXCLUIDO"): ("backup", "backup_excluido", "ok"),
    ("restauracao", "CONCLUIDO"): ("restauracao", "restauracao_concluida", "ok"),
    ("restauracao", "REVERTIDO"): ("restauracao", "restauracao_revertida", "falha"),
    ("restauracao", "FALHOU"): ("restauracao", "restauracao_falhou", "falha"),
}


def _backup_observer(rec: dict) -> None:
    """Chamado pela Central de backups quando uma operação termina: vira linha no histórico de auditoria."""
    kind = "restauracao" if rec.get("kind") == "restauracao" else "backup"
    name = _BACKUP_EVENT_NAMES.get((kind, rec.get("state")))
    if not name:
        return
    category, action, result = name
    detail = rec.get("error_message") or (f"tipo {rec.get('kind')}" + (f", {rec.get('size_bytes', 0)} bytes" if kind == "backup" else ""))
    _record(category, action, result, actor=str(rec.get("origin") or "painel")[:40], target=str(rec.get("slug", "")), detail=str(detail), ref=str(rec.get("id", ""))[:12])



class RateLimiter:
    """Bloqueia uma chave (ex.: um IP) depois de N falhas seguidas numa janela.

    A tentativa é contada ANTES de verificar a senha (a verificação é lenta de propósito), então
    pedidos simultâneos não passam do limite. Se a senha estava certa, a tentativa é devolvida:
    só falhas contam.
    """

    def __init__(self, limit: int, window_seconds: int):
        self.limit, self.window = limit, window_seconds
        self._state: dict[str, tuple[int, float, float]] = {}  # chave -> (falhas, bloqueado_até, início)
        self._lock = threading.Lock()

    def reserve(self, key: str) -> bool:
        now = time.time()
        with self._lock:
            count, blocked_until, start = self._state.get(key, (0, 0.0, now))
            if now < blocked_until:
                return False
            if blocked_until or now - start > self.window:
                count, blocked_until, start = 0, 0.0, now
            count += 1
            if count >= self.limit:
                blocked_until = now + self.window
            if len(self._state) > 5000:
                self._state = {k: v for k, v in self._state.items() if v[1] > now or now - v[2] <= self.window}
            self._state[key] = (count, blocked_until, start)
            return True

    def refund(self, key: str) -> None:
        with self._lock:
            entry = self._state.get(key)
            if entry and entry[0] > 0:
                count, blocked_until, start = entry[0] - 1, entry[1], entry[2]
                if count < self.limit:
                    blocked_until = 0.0
                self._state[key] = (count, blocked_until, start)


CLIENT_LOGIN_LIMIT = RateLimiter(10, 300)


class PanelMixin:
    """Painel do cliente da loja, servido pelo servidor de sites em /_painel/.

    O cliente chega nele por um botão escondido no próprio site (/_cdbs/admin.js) ou pelo link direto
    /_painel/?loja=NOME. Cada cliente só enxerga a própria loja: o nome da loja vem do cookie assinado,
    nunca do pedido. O painel só pode ser embutido em páginas deste mesmo endereço (frame-ancestors 'self').
    """

    PANEL_ASSETS = {
        "/": ("index.html", "text/html; charset=utf-8"),
        "/index.html": ("index.html", "text/html; charset=utf-8"),
        "/app.css": ("app.css", "text/css; charset=utf-8"),
        "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    }
    PRODUCT_ROUTE = re.compile(r"/api/products/([0-9a-f]{12})(/image)?")

    def _https(self) -> bool:
        return self.headers.get("X-Forwarded-Proto", "").lower() == "https"

    def _pheaders(self, cache: str = "no-store") -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", cache)
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob: data:; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'self'")
        if self._https():
            self.send_header("Strict-Transport-Security", "max-age=31536000")

    def _pjson(self, data: object, status: int = 200, cookie: str | None = None) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._pheaders()
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    def _perror(self, message: str, status: int = 400) -> None:
        self._pjson({"error": message}, status)

    def _cookie(self, value: str, max_age: int) -> str:
        # Path=/_painel: o navegador só envia o cookie para o painel, nunca para as páginas do site.
        return f"cdbs_loja={value}; HttpOnly; SameSite=Strict; Path={PANEL_PREFIX}; Max-Age={max_age}" + ("; Secure" if self._https() else "")

    def _origin_ok(self) -> bool:
        """Só aceita alterações vindas deste próprio endereço (Origin igual ao Host), nunca de outro site."""
        origin, host = urlsplit(self.headers.get("Origin", "")), self.headers.get("Host", "")
        return bool(host) and origin.scheme in ("http", "https") and origin.netloc.lower() == host.lower()

    def _body_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise CDBSError("Pedido inválido.") from None
        if length <= 0 or length > 16_384:
            raise CDBSError("Pedido inválido.", 413)
        try:
            data = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise CDBSError("Dados inválidos.") from None
        if not isinstance(data, dict):
            raise CDBSError("Dados inválidos.")
        return data

    def _body_bytes(self, limit: int) -> bytes:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise CDBSError("Pedido inválido.") from None
        if length <= 0 or length > limit:
            raise CDBSError("A foto pode ter até 2 MB.", 413)
        data = self.rfile.read(length)
        if len(data) != length:
            raise CDBSError("Upload incompleto.")
        return data

    def _session_slug(self) -> str | None:
        match = re.search(r"(?:^|;\s*)cdbs_loja=([A-Za-z0-9._-]+)", self.headers.get("Cookie", ""))
        return get_catalog().read_session(match.group(1)) if match else None

    def _require(self) -> str | None:
        slug = self._session_slug()
        if not slug:
            self._perror("Entre novamente.", 401)
        return slug

    def _store_info(self, slug: str) -> dict:
        info = get_catalog().get_store(slug)
        info["public_url"] = f"{PUBLIC_BASE}/{slug}/" if PUBLIC_BASE else ""
        return info

    @staticmethod
    def _panel_product(product: dict) -> dict:
        view = catalog.Catalog.panel_view(product)
        view["image_url"] = f"{PANEL_PREFIX}/media/{view['image']}" if view["image"] else None
        return view

    def _listing(self, slug: str) -> dict:
        products = [self._panel_product(item) for item in get_catalog().products(slug)]
        categories = sorted({item["category"] for item in products if item["category"]}, key=str.lower)
        return {"store": self._store_info(slug), "products": products, "categories": categories,
                "limits": {"products": catalog.MAX_PRODUCTS}}

    def _serve_asset(self, name: str, content_type: str) -> None:
        try:
            body = (CLIENT_WEB / name).read_bytes()
        except OSError:
            self._perror("Interface indisponível.", 500)
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self._pheaders()
        self.end_headers()
        self.wfile.write(body)

    def _serve_media(self, slug: str, name: str) -> None:
        path = get_catalog().media_path(slug, name)
        if path is None:
            self._perror("Foto não encontrada.", 404)
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", catalog.IMAGE_TYPES[path.suffix[1:]])
        self.send_header("Content-Length", str(len(body)))
        self._pheaders("private, max-age=31536000, immutable")
        self.end_headers()
        self.wfile.write(body)

    def _panel_get(self, rel: str) -> None:
        """GET em /_painel<rel>."""
        if rel == "":
            query = urlsplit(self.path).query
            self._reply(301, b"", extra={"Location": f"{PANEL_PREFIX}/" + (f"?{query}" if query else "")})
        elif rel in self.PANEL_ASSETS:
            self._serve_asset(*self.PANEL_ASSETS[rel])
        elif rel == "/api/session":
            slug = self._session_slug()
            data: dict = {"authenticated": bool(slug)}
            if slug:
                data["store"] = self._store_info(slug)
            self._pjson(data)
        elif rel == "/api/products" or rel.startswith("/media/"):
            slug = self._require()
            if not slug:
                return
            try:
                if rel == "/api/products":
                    self._pjson(self._listing(slug))
                else:
                    self._serve_media(slug, rel[len("/media/"):])
            except catalog.CatalogError as exc:
                self._perror(str(exc), exc.status)
        else:
            self._perror("Não encontrado.", 404)

    def _login(self) -> None:
        # Atrás do túnel todo pedido chega de 127.0.0.1; o IP real vem no cabeçalho do Cloudflare.
        ip = (self.headers.get("CF-Connecting-IP") or self.client_address[0])[:64]
        if not CLIENT_LOGIN_LIMIT.reserve(ip):
            raise CDBSError("Muitas tentativas. Tente novamente em alguns minutos.", 429)
        data = self._body_json()
        slug, password = data.get("slug"), data.get("password")
        if not (isinstance(slug, str) and isinstance(password, str) and 0 < len(password) <= 64):
            raise CDBSError("Loja ou senha incorretos.", 401)
        slug = slug.strip().lower()
        if not get_catalog().verify_login(slug, password):
            raise CDBSError("Loja ou senha incorretos.", 401)
        CLIENT_LOGIN_LIMIT.refund(ip)
        token = get_catalog().make_session(slug)
        self._pjson({"ok": True, "store": self._store_info(slug)}, cookie=self._cookie(token, catalog.SESSION_SECONDS))

    def _panel_write(self, method: str, rel: str) -> None:
        """POST/PUT/DELETE em /_painel<rel>. Todas exigem Origin igual ao Host; só o login dispensa a sessão."""
        if not self._origin_ok():
            self._perror("Origem não permitida.", 403)
            return
        try:
            if method == "POST" and rel == "/api/login":
                self._login()
                return
            if method == "POST" and rel == "/api/logout":
                self._pjson({"ok": True}, cookie=self._cookie("", 0))
                return
            slug = self._require()
            if not slug:
                return
            store_catalog = get_catalog()
            if method == "POST" and rel == "/api/products":
                product = store_catalog.add_product(slug, self._body_json())
                self._pjson({"ok": True, "product": self._panel_product(product)}, 201)
                return
            match = self.PRODUCT_ROUTE.fullmatch(rel)
            if not match:
                self._perror("Não encontrado.", 404)
                return
            product_id, image = match.group(1), bool(match.group(2))
            if method == "PUT" and image:
                product = store_catalog.set_image(slug, product_id, self._body_bytes(catalog.MAX_IMAGE_BYTES))
            elif method == "PUT":
                product = store_catalog.update_product(slug, product_id, self._body_json())
            elif method == "DELETE" and image:
                product = store_catalog.remove_image(slug, product_id)
            elif method == "DELETE":
                store_catalog.delete_product(slug, product_id)
                self._pjson({"ok": True})
                return
            else:
                self._perror("Não encontrado.", 404)
                return
            self._pjson({"ok": True, "product": self._panel_product(product)})
        except (catalog.CatalogError, CDBSError) as exc:
            self._perror(str(exc), exc.status)


class _LimitedReader:
    """Entrega exatamente N bytes do pedido ao http.client (ele lê até acabar; o socket do cliente não "acaba" sozinho)."""

    def __init__(self, source, length: int):
        self.source, self.left = source, length

    def read(self, size: int = -1) -> bytes:
        if self.left <= 0:
            return b""
        size = self.left if size is None or size < 0 else min(size, self.left)
        chunk = self.source.read(size)
        self.left -= len(chunk)
        if not chunk:
            self.left = 0
        return chunk


class PublicHandler(PanelMixin, DeadlineMixin, BaseHTTPRequestHandler):
    server_version = "CDBS-sites"

    def log_message(self, fmt: str, *args: object) -> None:
        return

    def do_HEAD(self) -> None:
        self._serve(head=True)

    def do_GET(self) -> None:
        self._serve(head=False)

    def _not_allowed(self) -> None:
        self._reply(405, b"Method not allowed", extra={"Allow": "GET, HEAD"})

    def _write(self) -> None:
        """Os sites são só leitura. Exceções: o painel do cliente (/_painel/) e o webhook ASSINADO do WhatsApp."""
        path = unquote(urlsplit(self.path).path)
        if path == WEBHOOK_PATH and self.command == "POST":
            self._webhook_post()
            return
        if path.startswith(PANEL_PREFIX + "/"):
            self._panel_write(self.command, path[len(PANEL_PREFIX):])
            return
        first = path.strip("/").split("/")[0] if path.strip("/") else ""
        if first and self._is_app(first):
            self._proxy(first, self.command)
        else:
            self._not_allowed()

    do_POST = _write
    do_PUT = _write
    do_DELETE = _write
    def _app_only(self) -> None:
        """PATCH e OPTIONS: só para servidores (apps). Sites e o painel do cliente continuam recusando (405), como na 3.0."""
        path = unquote(urlsplit(self.path).path)
        first = path.strip("/").split("/")[0] if path.strip("/") else ""
        if first and self._is_app(first):
            self._proxy(first, self.command)
        else:
            self._not_allowed()

    do_PATCH = _app_only
    do_OPTIONS = _app_only

    # ------------------------------------------------------------------ servidores (apps), 4.0
    HOP_BY_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer", "trailers",
                  "transfer-encoding", "upgrade", "proxy-connection"}

    @staticmethod
    def _is_app(name: str) -> bool:
        return bool(apps_mod.NAME_RE.fullmatch(name)) and (DATA / "apps" / name / "app.json").is_file() and not (SITES / name).is_dir()

    def _app_page(self, status: int, title: str, text: str, head: bool = False, extra: dict | None = None) -> None:
        body = (f"<!doctype html><html lang=pt-BR><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
                f"<title>{title}</title><body style='font:16px/1.5 system-ui,sans-serif;max-width:520px;margin:12vh auto;padding:0 20px;color:#18263a'>"
                f"<h1 style='font-size:22px'>{title}</h1><p>{text}</p></body></html>").encode()
        self._reply(status, body, "text/html; charset=utf-8", head=head, extra={"Cache-Control": "no-store", "Retry-After": "30", **(extra or {})})

    def _proxy(self, name: str, method: str) -> None:
        """Repassa o pedido para o servidor (127.0.0.1:<porta>) e devolve a resposta, em pedaços (sem guardar tudo na memória)."""
        head = method == "HEAD"
        try:
            cfg = get_apps().get(name)
        except apps_mod.AppError:
            self._reply(404, b"Nao encontrado", head=head)
            return
        split = urlsplit(self.path)
        prefix = f"/{name}"
        if split.path == prefix and method in ("GET", "HEAD"):
            self._reply(301, b"", head=head, extra={"Location": prefix + "/" + (f"?{split.query}" if split.query else "")})
            return
        if not cfg["enabled"]:
            self._app_page(503, "Fora do ar", "Este serviço está desligado no momento. Tente de novo mais tarde.", head)
            return
        if "upgrade" in (self.headers.get("Connection", "").lower()) or self.headers.get("Upgrade"):
            self._app_page(501, "Não suportado", "Conexões WebSocket ainda não são repassadas pelo CDBS.", head)
            return
        if "chunked" in self.headers.get("Transfer-Encoding", "").lower():
            self._app_page(411, "Tamanho obrigatório", "Envie o pedido com Content-Length.", head)
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length < 0 or length > int(cfg.get("max_body_mb") or 25) * 1024 * 1024:
            self._app_page(413, "Envio grande demais", f"O limite deste serviço é {cfg.get('max_body_mb') or 25} MB por pedido.", head)
            return
        upstream = split.path[len(prefix):] if cfg.get("strip_prefix", True) else split.path
        upstream = (upstream if upstream.startswith("/") else "/" + upstream) + (f"?{split.query}" if split.query else "")
        headers = {}
        for key, value in self.headers.items():
            low = key.lower()
            if low in self.HOP_BY_HOP or low in ("host", "content-length", "x-forwarded-prefix"):
                continue
            headers[key] = value
        client = self.headers.get("CF-Connecting-IP") or self.client_address[0]
        previous = self.headers.get("X-Forwarded-For")
        headers["X-Forwarded-For"] = f"{previous}, {client}" if previous else client
        headers["X-Forwarded-Proto"] = self.headers.get("X-Forwarded-Proto") or ("https" if self.headers.get("CF-Connecting-IP") else "http")
        headers["X-Forwarded-Host"] = self.headers.get("Host", "")
        headers["X-Forwarded-Prefix"] = prefix
        headers["Host"] = self.headers.get("Host") or f"127.0.0.1:{cfg['port']}"
        if length:
            headers["Content-Length"] = str(length)
        connection = http.client.HTTPConnection("127.0.0.1", cfg["port"], timeout=int(cfg.get("timeout_s") or 60))
        try:
            body = _LimitedReader(self.rfile, length) if length else None
            connection.request(method, upstream, body=body, headers=headers, encode_chunked=False)
            response = connection.getresponse()
        except (ConnectionRefusedError, FileNotFoundError):
            connection.close()
            self._app_page(502, "Serviço iniciando ou fora do ar", "O serviço não respondeu agora. Tente de novo em alguns segundos.", head)
            return
        except (socket.timeout, TimeoutError):
            connection.close()
            self._app_page(504, "Demorou demais", "O serviço demorou demais para responder.", head)
            return
        except (OSError, http.client.HTTPException):
            connection.close()
            self._app_page(502, "Serviço indisponível", "Não foi possível falar com o serviço agora.", head)
            return
        try:
            self.send_response(response.status, response.reason)
            has_length = False
            for key, value in response.getheaders():
                low = key.lower()
                if low in self.HOP_BY_HOP or low in ("server", "date"):
                    continue
                if low == "content-length":
                    has_length = True
                if low == "location" and cfg.get("strip_prefix", True):
                    value = self._rewrite_location(value, prefix, cfg["port"])
                if low == "set-cookie" and cfg.get("strip_prefix", True):
                    value = re.sub(r"(?i)(;\s*path=)/(?=\s*;|\s*$)", rf"\1{prefix}/", value)
                self.send_header(key, value)
            if not response.getheader("X-Content-Type-Options"):
                self.send_header("X-Content-Type-Options", "nosniff")
            if not has_length:
                self.send_header("Connection", "close")
                self.close_connection = True
            self.end_headers()
            if not head:
                while True:
                    chunk = response.read1(64 * 1024) if hasattr(response, "read1") else response.read(64 * 1024)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError, socket.timeout, OSError, http.client.HTTPException):
            self.close_connection = True
        finally:
            connection.close()

    @staticmethod
    def _rewrite_location(value: str, prefix: str, port: int) -> str:
        """O servidor acha que mora em "/": devolve os redirecionamentos para dentro de /<nome>/."""
        for local in (f"http://127.0.0.1:{port}", f"http://localhost:{port}"):
            if value.startswith(local):
                value = value[len(local):] or "/"
        if value.startswith("/") and not value.startswith("//") and value != prefix and not value.startswith(prefix + "/"):
            return prefix + value
        return value

    def _reply(self, status: int, body: bytes, content_type: str = "text/plain; charset=utf-8", head: bool = False, extra: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        if extra:
            for key, value in extra.items():
                self.send_header(key, value)
        self.end_headers()
        if not head:
            self.wfile.write(body)

    def _start(self, status: int, extra: dict[str, str] | None = None, content_type: str | None = None, length: int | None = None, cache: str = "no-cache") -> None:
        self.send_response(status)
        if content_type:
            self.send_header("Content-Type", content_type)
        if length is not None:
            self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", cache)  # "no-cache" = pode guardar, mas revalide (ETag → 304)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()

    def _not_modified(self, etag: str, mtime: float | None = None) -> bool:
        match = self.headers.get("If-None-Match")
        if match is not None:
            tokens = [token.strip() for token in match.split(",")]
            return "*" in tokens or any(token.removeprefix("W/") == etag for token in tokens)
        since = self.headers.get("If-Modified-Since")
        if since and mtime is not None:
            try:
                return int(mtime) <= email.utils.parsedate_to_datetime(since).timestamp()
            except (TypeError, ValueError):
                return False
        return False

    def _if_range_ok(self, etag: str, last_modified: str) -> bool:
        value = self.headers.get("If-Range")
        return value is None or value.strip() in (etag, last_modified)

    def _serve(self, head: bool) -> None:
        raw = unquote(urlsplit(self.path).path)
        if "\\" in raw or "\x00" in raw:
            self._reply(404, b"Nao encontrado", head=head)
            return
        parts = raw.strip("/").split("/") if raw.strip("/") else []
        if parts == ["_cdbs", "ping"]:
            self._reply(200, b"ok", head=head, extra={"Cache-Control": "no-store"})
            return
        if parts == ["_whatsapp", "webhook"]:
            self._webhook_get(head)
            return
        if parts and parts[0] in ("_api", "_media", "_cdbs", "_painel", "_whatsapp"):  # nomes que nenhum site pode ter (slug não começa com "_")
            self._serve_reserved(parts, head)
            return
        if parts and self._is_app(parts[0]):
            self._proxy(parts[0], "HEAD" if head else "GET")
            return
        if not parts or not valid_slug(parts[0]) or any(p in (".", "..") or p.startswith(".") for p in parts[1:]):
            self._reply(404, b"Nao encontrado", head=head)
            return
        slug = parts[0]
        if self._store_unavailable(slug, head):
            return
        if len(parts) == 1 and not raw.endswith("/"):
            self._reply(301, b"", head=head, extra={"Location": f"/{slug}/"})
            return
        sites_root = SITES.resolve()
        root = (SITES / slug).resolve()
        if not root.is_dir() or sites_root not in root.parents:
            self._reply(404, b"Site nao encontrado", head=head)
            return
        relative = parts[1:] or ["index.html"]
        target = (root.joinpath(*relative)).resolve()
        if target != root and root not in target.parents:
            self._reply(404, b"Nao encontrado", head=head)
            return
        if target.is_dir():
            target = target / "index.html"
        if not target.is_file() or target.name.startswith(".") or target.suffix.lower() in BLOCKED_EXT:
            self._reply(404, b"Arquivo nao encontrado", head=head)
            return
        self._send_file(target, head)

    def _webhook_get(self, head: bool) -> None:
        """Aperto de mão da Meta ao cadastrar o webhook: devolve o hub.challenge só se o token de verificação conferir."""
        challenge = whatsapp.handshake(parse_qs(urlsplit(self.path).query), get_settings().secret("whatsapp_verify_token"))
        if challenge is None:
            _webhook_refused("verificação do webhook com token errado ou ausente")
            self._reply(403, b"", head=head, extra={"Cache-Control": "no-store"})
            return
        _record("bot", "webhook_verificado", "ok", actor="meta", detail="a Meta confirmou o endereço do webhook")
        self._reply(200, challenge.encode("ascii"), head=head, extra={"Cache-Control": "no-store"})

    def _webhook_post(self) -> None:
        """Eventos do WhatsApp. Sem assinatura válida (chave do app) NADA é lido. Responde rápido; o bot trabalha depois."""
        if not WEBHOOK_GATE.allow():
            self._reply(429, b"", extra={"Retry-After": "60"})
            return
        raw = self.headers.get("Content-Length", "")
        if not raw.isdigit() or int(raw) <= 0:
            self._reply(400, b"")
            return
        if int(raw) > WEBHOOK_MAX:
            self._reply(413, b"")
            return
        body = self.rfile.read(int(raw))
        secret = get_settings().secret("whatsapp_app_secret")
        if not whatsapp.verify_signature(secret, body, self.headers.get("X-Hub-Signature-256")):
            _webhook_refused("assinatura inválida" if secret else "chave do app (App Secret) não configurada")
            self._reply(401 if secret else 503, b"")
            return
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            self._reply(400, b"")
            return
        _safe(lambda: get_bot().handle_payload(payload))
        self._reply(200, b"ok", extra={"Cache-Control": "no-store"})

    def _store_unavailable(self, slug: str, head: bool) -> bool:
        """Responde no lugar do arquivo quando a loja não deve aparecer: em manutenção (troca de arquivos de um backup,
        janela de milissegundos) devolve 503 + Retry-After em vez de um 404 enganoso; loja recuperada e ainda sem nova senha fica invisível (404)."""
        if get_backup().in_maintenance(slug):
            self._reply(503, "Site em manutenção por alguns segundos. Tente de novo.".encode("utf-8"), head=head, extra={"Retry-After": "5"})
            return True
        if get_publication().is_paused(slug):
            self._reply(503, PAUSED_PAGE, "text/html; charset=utf-8", head=head, extra={"Retry-After": "3600"})
            return True
        if get_catalog().is_pending(slug):
            self._reply(404, b"Site nao encontrado", head=head)
            return True
        return False

    def _serve_reserved(self, parts: list[str], head: bool) -> None:
        """/_api/<loja>/products.json, /_media/<loja>/<foto>, /_painel/ (painel do cliente) e /_cdbs/*.js (ajudantes)."""
        area = parts[0]
        if area in ("_api", "_media") and len(parts) > 1 and valid_slug(parts[1]) and self._store_unavailable(parts[1], head):
            return
        if area == "_painel":
            if head:
                self._reply(405, b"", head=True, extra={"Allow": "GET, POST, PUT, DELETE"})
            else:
                self._panel_get(unquote(urlsplit(self.path).path)[len(PANEL_PREFIX):])
            return
        if area == "_cdbs" and parts[1:] in (["catalog.js"], ["admin.js"]):
            self._send_file(WEB / parts[1], head)
            return
        if area == "_api" and len(parts) == 3 and parts[2] == "products.json" and valid_slug(parts[1]):
            self._serve_products(parts[1], head)
            return
        if area == "_media" and len(parts) == 3:
            path = get_catalog().media_path(parts[1], parts[2])
            if path is not None:
                # O nome da foto é aleatório e nunca muda de conteúdo: pode ficar em cache "para sempre".
                self._send_file(path, head, "public, max-age=31536000, immutable", {"Content-Security-Policy": "default-src 'none'; sandbox"})
                return
        self._reply(404, b"Nao encontrado", head=head)

    def _serve_products(self, slug: str, head: bool) -> None:
        try:
            data = get_catalog().public_data(slug)
        except catalog.CatalogError:
            self._reply(404, b"Catalogo nao encontrado", head=head)
            return
        body = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        etag = '"' + hashlib.sha256(body).hexdigest()[:32] + '"'
        extra = {"ETag": etag, "Access-Control-Allow-Origin": "*"}  # são dados públicos; o site pode estar em outro endereço
        if self._not_modified(etag):
            self._start(304, extra)
            return
        self._start(200, extra, "application/json; charset=utf-8", len(body))
        if not head:
            self.wfile.write(body)

    def _send_file(self, target: Path, head: bool, cache: str = "no-cache", extra_headers: dict[str, str] | None = None) -> None:
        try:
            source = target.open("rb")
            info = os.fstat(source.fileno())
        except OSError:
            self._reply(404, b"Arquivo nao encontrado", head=head)
            return
        size = info.st_size
        etag = f'"{size:x}-{info.st_mtime_ns:x}"'
        last_modified = email.utils.formatdate(info.st_mtime, usegmt=True)
        validators = {"ETag": etag, "Last-Modified": last_modified}

        if self._not_modified(etag, info.st_mtime):
            source.close()
            self._start(304, validators, cache=cache)
            return

        start, end, status = 0, size - 1, 200
        wanted = self.headers.get("Range", "")
        if wanted.startswith("bytes=") and self._if_range_ok(etag, last_modified):
            parsed = _parse_range(wanted[6:], size)
            if parsed is False:
                source.close()
                self._start(416, {"Content-Range": f"bytes */{size}"}, length=0)
                return
            if parsed:
                (start, end), status = parsed, 206
        length = end - start + 1

        extra = {**validators, "Accept-Ranges": "bytes", **(extra_headers or {})}
        if status == 206:
            extra["Content-Range"] = f"bytes {start}-{end}/{size}"
        self._start(status, extra, _content_type(target), length, cache)
        if head:
            source.close()
            return
        try:
            with source:
                source.seek(start)
                remaining = length
                while remaining > 0 and (chunk := source.read(min(64 * 1024, remaining))):
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError, socket.timeout):
            return


def start_reminder_thread(interval: float | None = None) -> threading.Thread:
    """Lembrete mensal: verifica ao iniciar e a cada 6 h. Só ANOTA o estado na Central (nunca bloqueia publicação nem suspende lojas).
    `thread.stop_event.set()` encerra o laço (usado ao desligar e nos testes)."""
    every = interval if interval is not None else backup.REMINDER_TICK_SECONDS
    stop = threading.Event()

    def loop() -> None:
        source = "inicio"
        while not stop.is_set():
            try:
                get_backup().reminder_tick(source)
            except Exception:  # noqa: BLE001 — um erro aqui nunca pode derrubar o servidor
                pass
            source = "periodico"
            stop.wait(every)

    thread = threading.Thread(target=loop, name="cdbs-lembrete", daemon=True)
    thread.stop_event = stop  # type: ignore[attr-defined]
    thread.start()
    return thread


PAUSED_PAGE = ("<!doctype html><html lang=pt-BR><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
               "<title>Site temporariamente fora do ar</title><body style='font-family:system-ui,sans-serif;max-width:32rem;margin:15vh auto;padding:0 1rem;color:#222'>"
               "<h1 style='font-size:1.4rem'>Site temporariamente fora do ar</h1><p>Este site está pausado no momento. Tente de novo mais tarde.</p></body></html>").encode("utf-8")
EXIT_CANNOT_START = 3    # o supervisor distingue "não consegue iniciar" (porta ocupada, pasta sem permissão) de queda


def _bind(port: int, handler: type, label: str) -> LocalOnlyServer:
    try:
        return LocalOnlyServer(("127.0.0.1", port), handler)
    except OSError as exc:
        busy = getattr(exc, "errno", None) in (98, 48, 10048)       # EADDRINUSE (Linux/Android, macOS, Windows)
        reason = (f"a porta {port} ({label}) já está em uso. Provavelmente outro CDBS já está rodando em outra sessão do Termux "
                  f"— feche-o (Ctrl+C) ou use: python3 supervisor.py status") if busy else f"não foi possível abrir a porta {port} ({label}): {exc.strerror or exc}"
        raise SystemExit(f"CDBS.OS não iniciou: {reason}") from None


def startup_checks() -> None:
    """Antes de abrir as portas: a pasta de dados precisa aceitar gravação."""
    try:
        DATA.mkdir(parents=True, exist_ok=True)
        SITES.mkdir(parents=True, exist_ok=True)
        probe = DATA / f".start-{os.getpid()}.tmp"
        probe.write_bytes(b"ok")
        probe.unlink()
    except OSError as exc:
        raise SystemExit(f"CDBS.OS não iniciou: não consigo gravar na pasta de dados ({exc.strerror or exc}). "
                         "Confira o espaço livre e as permissões; rode: python3 cdbs.py doctor") from None


def run() -> None:
    global STARTED_AT
    # O pedido de parada (SIGTERM, do supervisor) é atendido desde o PRIMEIRO instante. Antes ele só era instalado
    # depois de subir tudo: um SIGTERM logo depois de o servidor responder (reinício/atualização rápidos) matava o
    # processo sem desligamento limpo, e o próximo início avisava um "encerramento inesperado" que não existiu.
    stop = threading.Event()
    try:
        import signal
        signal.signal(signal.SIGTERM, lambda *_: stop.set())
    except (ImportError, ValueError, OSError):
        pass
    try:
        startup_checks()
        admin = _bind(ADMIN_PORT, AdminHandler, "painel")
        try:
            public = _bind(SITE_PORT, PublicHandler, "sites")
        except SystemExit:
            admin.server_close()
            raise
    except SystemExit as stop:
        _record("servico", "inicio_falhou", "falha", actor="sistema", detail=str(stop))
        print(stop, file=sys.stderr)
        sys.exit(EXIT_CANNOT_START)
    STARTED_AT = int(time.time())
    center = _safe(get_notifications)                # antes dos registros de início: assim "encerramento inesperado" também vira aviso
    beat = lifecycle.Heartbeat(DATA / "system" / "cdbs-heartbeat.json")
    previous = beat.previous()
    beat.start(version=APP_VERSION, supervised=bool(os.environ.get("CDBS_SUPERVISED")))
    if previous:
        _record("servico", "encerramento_inesperado", "aviso", actor="sistema", detail=previous["message"])
        print(f"Atenção: {previous['message']}")
    _record("servico", "servico_iniciado", "ok", actor="sistema", detail=f"versão {APP_VERSION}")
    recovered = get_backup().recover()  # o Android pode ter encerrado o Termux no meio de um backup/restauração
    if recovered["interrupted"] or recovered["reverted"]:
        print(f"Atenção: {recovered['interrupted']} operação(ões) de backup foram interrompidas e {recovered['reverted']} restauração(ões) foram desfeitas com segurança. Veja a Central de backups.")
    if recovered.get("cleaned"):
        print(f"Limpeza: {recovered['cleaned']} pasta(s) temporária(s) órfã(s) de restaurações interrompidas foram removidas.")
    reminder = start_reminder_thread()
    for server in (admin, public):
        threading.Thread(target=server.serve_forever, daemon=True).start()
    operations = _safe(get_ops)
    if center is not None:
        _safe(center.start)
    if operations is not None:
        _safe(operations.monitor.start)
    bot_worker = _safe(get_bot)
    if bot_worker is not None:
        _safe(bot_worker.start)
    print(f"CDBS.OS {APP_VERSION} iniciado. Nenhuma porta está aberta diretamente para a internet.")
    print(f"Painel local: http://127.0.0.1:{ADMIN_PORT}/")
    print(f"Sites locais: http://127.0.0.1:{SITE_PORT}/<nome>/")
    print(f"Painel dos clientes: http://127.0.0.1:{SITE_PORT}{PANEL_PREFIX}/?loja=NOME  (fica dentro do endereço dos sites)")
    if PUBLIC_BASE:
        print(f"Base pública configurada: {PUBLIC_BASE}")
    else:
        print("Link público: ainda não configurado (não compartilhe os endereços 127.0.0.1).")
    print("Para parar: Ctrl+C. O painel administrativo é somente local.")
    _watch_supervisor(stop)
    try:
        while not stop.wait(1):
            pass
    except KeyboardInterrupt:
        print("\nEncerrando CDBS.OS...")
    finally:
        reminder.stop_event.set()  # type: ignore[attr-defined]
        if operations is not None:
            _safe(operations.monitor.stop)
        if bot_worker is not None:
            _safe(bot_worker.stop)
        if center is not None:
            _safe(center.stop)
        for server in (admin, public):
            server.shutdown()
        for server in (admin, public):
            server.server_close()
        _record("servico", "servico_parado", "ok", actor="sistema", detail="desligamento normal")
        beat.stop(clean=True)


def _watch_supervisor(stop: threading.Event) -> None:
    """Se o supervisor que iniciou este servidor sumir (morto à força), o servidor também sai em poucos segundos:
    assim não sobra um processo segurando as portas quando você der bash start.sh de novo."""
    try:
        pid = int(os.environ.get("CDBS_SUPERVISOR_PID", "0") or 0)
    except ValueError:
        pid = 0
    if pid <= 1:
        return

    def loop() -> None:
        while not stop.wait(5):
            try:
                os.kill(pid, 0)
            except OSError:
                print("O supervisor parou de existir; encerrando o servidor também.", flush=True)
                stop.set()

    threading.Thread(target=loop, name="cdbs-vigia-supervisor", daemon=True).start()


def main(argv: list[str]) -> None:
    command = argv[1] if len(argv) > 1 else "serve"
    if command == "serve":
        run()
    else:
        print("Uso: python3 cdbs.py [serve | doctor [--json] [--offline] [--corrigir]]", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main(sys.argv)
