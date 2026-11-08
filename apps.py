"""CDBS.OS — servidores (aplicativos que NÃO são estáticos), desde a 4.0.

Um "servidor" é um programa seu — em Python ou Node.js — que fica rodando e responde pedidos HTTP. O CDBS:
  • guarda até APP_SLOTS (5) servidores, cada um numa VAGA com porta fixa só no celular (127.0.0.1:8101…8105);
  • publica cada um no MESMO túnel dos sites, no endereço /<nome>/ (o servidor dos sites repassa os pedidos);
  • inicia, vigia e reinicia pelo supervisor (o "guardião"), com a mesma regra de espera crescente e limite de falhas
    do servidor do CDBS — nunca entra em laço;
  • implanta código novo com volta automática: se a versão nova não responder, a anterior é recolocada;
  • guarda a saída de cada servidor (registro com rotação) e mostra memória, CPU e reinícios no painel.

Divisão de trabalho (os mesmos dois processos da 3.0):
  • AppStore  — cadastro e arquivos. Usada pelo servidor do CDBS (painel) e pelo supervisor.
  • AppRunner — processos. Só o supervisor usa: é ele quem inicia e para programas.

Pastas (tudo em data/, privado):
  data/apps/<nome>/app.json    configuração (vaga, tipo, arquivo de entrada, verificação de saúde, ligado/desligado)
  data/apps/<nome>/env.json    variáveis de ambiente (0600; valores nunca voltam para o painel)
  data/apps/<nome>/atual/      código em uso            data/apps/<nome>/anterior/  versão anterior (para voltar)
  data/apps/<nome>/novo/       código enviado, esperando a implantação
  data/apps/<nome>/dados/      pasta PERSISTENTE do servidor (CDBS_APP_DATA): sobrevive a implantações
  data/apps/<nome>/logs/       saida.log (+ .1 .2)
  data/system/apps.json        estado dos processos (gravado pelo supervisor)

Segurança — leia: um servidor roda com as MESMAS permissões do CDBS no celular (não é uma caixa de areia). Ele escuta só
em 127.0.0.1 e chega à internet apenas pelo repasse do CDBS, mas o código em si pode ler arquivos do Termux. Rode só
código seu ou que você revisou. Nenhum texto vindo do painel, do bot ou da internet vira comando de shell: o programa é
iniciado com uma lista fixa de argumentos ([python3, arquivo] ou [node, arquivo]).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import signal
import socket
import stat
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path, PurePosixPath
from typing import Callable
from zipfile import BadZipFile, ZipFile

from sysutil import ensure_private_dir, fmt_local, read_json, scrub, write_json_private

APP_SLOTS = 5
PORT_BASE_DEFAULT = 8101
NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,30}[a-z0-9])?$")       # mesma regra dos sites: os dois dividem o endereço
RESERVED = {"api", "admin", "static", "assets"}                           # evita confusão com caminhos comuns
RUNTIMES = {"python": "Python 3", "node": "Node.js"}
ENTRY_EXT = {"python": (".py",), "node": (".js", ".mjs", ".cjs")}
ENV_KEY_RE = re.compile(r"^[A-Z_][A-Z0-9_]{0,63}$")
ENV_PROTECTED = {"PORT", "HOST", "PATH", "HOME", "PWD", "LD_PRELOAD", "LD_LIBRARY_PATH", "PYTHONPATH", "NODE_OPTIONS", "TMPDIR"}
ENV_MAX_VARS, ENV_MAX_VALUE = 50, 4096
HEALTH_RE = re.compile(r"^/[A-Za-z0-9._~/-]{0,120}$")
MAX_ZIP = 200 * 1024 * 1024                  # 200 MB (com node_modules o ZIP cresce)
MAX_UNPACKED = 800 * 1024 * 1024             # 800 MB
MAX_FILES = 40_000
MAX_DEPTH = 40
LOG_MAX, LOG_KEEP = 512 * 1024, 2            # saida.log + saida.log.1 + saida.log.2
JUNK_NAMES = {".ds_store", "thumbs.db", "desktop.ini"}
JUNK_DIRS = {"__macosx", "__pycache__", ".git"}

# Vigilância (mesmas ideias do servidor do CDBS)
RESTART_DELAYS = (2, 5, 15, 30, 60, 120, 300)
CRASH_WINDOW, MAX_CRASHES, STABLE_AFTER = 900, 6, 600
HEALTH_EVERY, HEALTH_FAILS, START_GRACE = 15.0, 3, 30.0

STATE_LABELS = {
    "parado": "⏸ Parado", "iniciando": "🟡 Iniciando", "rodando": "🟢 Rodando", "reiniciando": "🟠 Reiniciando",
    "parado_por_falhas": "🔴 Parado por falhas", "sem_codigo": "⚪ Sem código", "implantando": "🟡 Implantando",
    "indisponivel": "🔴 Indisponível",
}


class AppError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def port_base(environ: dict | None = None) -> int:
    env = os.environ if environ is None else environ
    try:
        value = int(env.get("CDBS_APP_PORT_BASE", "") or PORT_BASE_DEFAULT)
    except ValueError:
        return PORT_BASE_DEFAULT
    return value if 1024 <= value <= 65535 - APP_SLOTS else PORT_BASE_DEFAULT


def label(state: str) -> str:
    return STATE_LABELS.get(state, state)


def tree_size(folder: Path) -> tuple[int, int]:
    total = files = 0
    if not folder.exists():
        return 0, 0
    for root, dirs, names in os.walk(folder):
        dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(root, d))]
        for name in names:
            try:
                info = os.lstat(os.path.join(root, name))
            except OSError:
                continue
            if stat.S_ISREG(info.st_mode):
                total += info.st_size
                files += 1
    return total, files


# =============================================================================================== cadastro e arquivos
class AppStore:
    def __init__(self, data: Path, *, clock: Callable[[], float] = time.time, environ: dict | None = None):
        self.data = data
        self.root = data / "apps"
        self.state_file = data / "system" / "apps.json"
        self._clock = clock
        self.port_base = port_base(environ)
        self._lock = threading.RLock()

    # ----- caminhos
    def folder(self, name: str) -> Path:
        if not isinstance(name, str) or not NAME_RE.fullmatch(name):
            raise AppError("Nome de servidor inválido.")
        return self.root / name

    def port(self, slot: int) -> int:
        return self.port_base + slot - 1

    # ----- leitura
    def _read(self, name: str) -> dict | None:
        cfg = read_json(self.folder(name) / "app.json", default=None)
        return cfg if isinstance(cfg, dict) and cfg.get("name") == name else None

    def list(self) -> list[dict]:
        if not self.root.is_dir():
            return []
        out = []
        for item in self.root.iterdir():
            if item.is_dir() and NAME_RE.fullmatch(item.name):
                cfg = self._read(item.name)
                if cfg:
                    out.append(self._public(cfg))
        return sorted(out, key=lambda c: c["slot"])

    def names(self) -> set[str]:
        return {c["name"] for c in self.list()}

    def get(self, name: str) -> dict:
        cfg = self._read(name)
        if not cfg:
            raise AppError("Servidor não encontrado.", 404)
        return self._public(cfg)

    def _public(self, cfg: dict) -> dict:
        folder = self.root / cfg["name"]
        return {**{k: cfg.get(k) for k in ("name", "slot", "runtime", "entry", "health_path", "strip_prefix", "enabled", "created_at",
                                            "deployed_at", "description", "max_body_mb", "timeout_s")},
                "port": self.port(cfg["slot"]), "has_code": (folder / "atual").is_dir(), "has_previous": (folder / "anterior").is_dir(),
                "has_staged": (folder / "novo").is_dir(), "env_keys": sorted(self._env(cfg["name"]).keys())}

    def free_slots(self) -> list[int]:
        used = {c["slot"] for c in self.list()}
        return [n for n in range(1, APP_SLOTS + 1) if n not in used]

    # ----- validação
    @staticmethod
    def _check_fields(runtime: object, entry: object, health_path: object) -> tuple[str, str, str]:
        if runtime not in RUNTIMES:
            raise AppError("Tipo inválido: escolha python ou node.")
        if not isinstance(entry, str) or not entry.strip():
            raise AppError("Informe o arquivo de entrada (ex.: app.py ou server.js).")
        entry = entry.strip()
        path = PurePosixPath(entry)
        if path.is_absolute() or ".." in path.parts or len(entry) > 120 or not re.fullmatch(r"[A-Za-z0-9._/-]+", entry):
            raise AppError("Arquivo de entrada inválido: use um caminho dentro do ZIP, como app.py ou src/server.js.")
        if not entry.endswith(ENTRY_EXT[runtime]):
            raise AppError(f"Para {RUNTIMES[runtime]}, o arquivo de entrada termina em {' ou '.join(ENTRY_EXT[runtime])}.")
        health = health_path if isinstance(health_path, str) else ""
        health = health.strip()
        if health and (not HEALTH_RE.fullmatch(health) or ".." in health):
            raise AppError("Caminho de saúde inválido (ex.: /saude). Deixe vazio para só conferir se a porta responde.")
        return runtime, entry, health

    def create(self, name: object, runtime: object, entry: object, *, health_path: object = "", strip_prefix: object = True,
               description: object = "", taken: Callable[[str], bool] = lambda _n: False) -> dict:
        if not isinstance(name, str) or not NAME_RE.fullmatch(name.strip().lower()):
            raise AppError("Nome inválido: use letras minúsculas, números e hífen (ex.: api-loja).")
        name = name.strip().lower()
        if name in RESERVED:
            raise AppError(f"O nome {name} é reservado. Escolha outro.")
        runtime, entry, health = self._check_fields(runtime, entry, health_path)
        with self._lock:
            if (self.root / name).exists():
                raise AppError("Já existe um servidor com este nome.", 409)
            if taken(name):
                raise AppError(f"Já existe um SITE chamado {name}. Sites e servidores dividem o endereço (/{name}/): escolha outro nome.", 409)
            free = self.free_slots()
            if not free:
                raise AppError(f"As {APP_SLOTS} vagas de servidor estão ocupadas. Remova um servidor para criar outro.", 409)
            folder = self.root / name
            ensure_private_dir(self.root)
            folder.mkdir(mode=0o700)
            for sub in ("dados", "logs"):
                (folder / sub).mkdir(mode=0o700)
            cfg = {"name": name, "slot": free[0], "runtime": runtime, "entry": entry, "health_path": health,
                   "strip_prefix": strip_prefix is not False, "enabled": False, "created_at": int(self._clock()), "deployed_at": None,
                   "description": scrub(description if isinstance(description, str) else "", 120), "max_body_mb": 25, "timeout_s": 60}
            write_json_private(folder / "app.json", cfg)
            return self._public(cfg)

    def update(self, name: str, changes: dict) -> dict:
        with self._lock:
            cfg = self._read(name)
            if not cfg:
                raise AppError("Servidor não encontrado.", 404)
            runtime = changes.get("runtime", cfg["runtime"])
            entry = changes.get("entry", cfg["entry"])
            health = changes.get("health_path", cfg.get("health_path", ""))
            cfg["runtime"], cfg["entry"], cfg["health_path"] = self._check_fields(runtime, entry, health)
            if "strip_prefix" in changes:
                cfg["strip_prefix"] = changes["strip_prefix"] is not False
            if "description" in changes and isinstance(changes["description"], str):
                cfg["description"] = scrub(changes["description"], 120)
            for key, low, high in (("max_body_mb", 1, 100), ("timeout_s", 5, 300)):
                if key in changes:
                    value = changes[key]
                    if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
                        raise AppError(f"{key}: use um número de {low} a {high}.")
                    cfg[key] = value
            write_json_private(self.folder(name) / "app.json", cfg)
            return self._public(cfg)

    def set_enabled(self, name: str, enabled: bool) -> None:
        with self._lock:
            cfg = self._read(name)
            if cfg:
                cfg["enabled"] = bool(enabled)
                write_json_private(self.folder(name) / "app.json", cfg)

    def mark_deployed(self, name: str) -> None:
        with self._lock:
            cfg = self._read(name)
            if cfg:
                cfg["deployed_at"] = int(self._clock())
                write_json_private(self.folder(name) / "app.json", cfg)

    # ----- variáveis de ambiente (valores nunca voltam para o painel)
    def _env(self, name: str) -> dict:
        env = read_json(self.folder(name) / "env.json", default={})
        return {k: v for k, v in env.items() if isinstance(k, str) and isinstance(v, str)} if isinstance(env, dict) else {}

    def env_for_process(self, name: str) -> dict:
        return {k: v for k, v in self._env(name).items() if ENV_KEY_RE.fullmatch(k) and k not in ENV_PROTECTED and not k.startswith("CDBS_")}

    def set_env(self, name: str, values: object, remove: object = ()) -> list[str]:
        if not isinstance(values, dict) or not all(isinstance(k, str) for k in values):
            raise AppError("Variáveis em formato inválido.")
        if not isinstance(remove, (list, tuple)):
            raise AppError("Lista de remoção inválida.")
        with self._lock:
            self.get(name)
            env = self._env(name)
            for key in remove:
                env.pop(key, None)
            for key, value in values.items():
                if not ENV_KEY_RE.fullmatch(key):
                    raise AppError(f"Nome de variável inválido: {scrub(key, 40)} (use MAIUSCULAS_COM_SUBLINHADO).")
                if key in ENV_PROTECTED or key.startswith("CDBS_"):
                    raise AppError(f"{key} é definida pelo CDBS e não pode ser trocada.")
                if not isinstance(value, str) or len(value) > ENV_MAX_VALUE or "\x00" in value:
                    raise AppError(f"Valor inválido para {key} (texto de até {ENV_MAX_VALUE} caracteres).")
                env[key] = value
            if len(env) > ENV_MAX_VARS:
                raise AppError(f"No máximo {ENV_MAX_VARS} variáveis por servidor.")
            write_json_private(self.folder(name) / "env.json", env)
            return sorted(env)

    # ----- código enviado (ZIP)
    def stage_zip(self, name: str, source, length: int) -> dict:
        """Lê o ZIP (até MAX_ZIP), valida TUDO e extrai para novo/. Nada do que está rodando é tocado aqui:
        a troca é feita pelo supervisor (implantação com volta automática)."""
        cfg = self._read(name)
        if not cfg:
            raise AppError("Servidor não encontrado.", 404)
        if length <= 0 or length > MAX_ZIP:
            raise AppError(f"Envie um ZIP de até {MAX_ZIP // (1024 * 1024)} MB.", 413)
        folder = self.folder(name)
        tmp_zip = folder / f".envio-{os.getpid()}-{threading.get_ident()}.zip"
        work = folder / f".novo-{os.getpid()}-{threading.get_ident()}"
        try:
            got = 0
            with open(tmp_zip, "wb") as out:
                while got < length:
                    chunk = source.read(min(1024 * 1024, length - got))
                    if not chunk:
                        break
                    out.write(chunk)
                    got += len(chunk)
            if got != length:
                raise AppError("Envio incompleto. Tente de novo.")
            try:
                archive = ZipFile(tmp_zip)
            except BadZipFile:
                raise AppError("O arquivo não é um ZIP válido.") from None
            with archive:
                entries = self._safe_entries(archive)
                if work.exists():
                    shutil.rmtree(work)
                work.mkdir(mode=0o700)
                total = 0
                for info, rel in entries:
                    target = work.joinpath(*rel.parts)
                    if info.is_dir():
                        target.mkdir(parents=True, exist_ok=True)
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(info) as src, open(target, "wb") as dst:
                        while chunk := src.read(256 * 1024):
                            total += len(chunk)
                            if total > MAX_UNPACKED:
                                raise AppError(f"O conteúdo descompactado passa de {MAX_UNPACKED // (1024 * 1024)} MB.", 413)
                            dst.write(chunk)
                    mode = (info.external_attr >> 16) & 0o777
                    os.chmod(target, 0o700 if mode & 0o100 else 0o600)
            entry_path = work.joinpath(*PurePosixPath(cfg["entry"]).parts)
            if not entry_path.is_file():
                raise AppError(f"O ZIP não tem o arquivo de entrada {cfg['entry']}. Confira o nome (ou mude o arquivo de entrada).")
            staged = folder / "novo"
            if staged.exists():
                shutil.rmtree(staged)
            os.replace(work, staged)
            size, files = tree_size(staged)
            return {"files": files, "bytes": size, "entry": cfg["entry"]}
        finally:
            tmp_zip.unlink(missing_ok=True)
            if work.exists():
                shutil.rmtree(work, ignore_errors=True)

    @staticmethod
    def _safe_entries(archive: ZipFile) -> list:
        infos = archive.infolist()
        if len(infos) > MAX_FILES:
            raise AppError(f"O ZIP tem arquivos demais (máximo {MAX_FILES}).")
        cleaned, declared = [], 0
        for info in infos:
            raw = info.filename.replace("\\", "/")
            path = PurePosixPath(raw)
            if not raw or path.is_absolute() or ":" in path.parts[0] or any(p in ("..", ".") for p in path.parts) or len(path.parts) > MAX_DEPTH:
                raise AppError(f"Caminho inseguro no ZIP: {scrub(info.filename, 80)}")
            if stat.S_ISLNK(info.external_attr >> 16):
                raise AppError(f"O ZIP tem um atalho (link simbólico), que não é aceito: {scrub(info.filename, 80)}")
            low = [p.lower() for p in path.parts]
            if any(p in JUNK_DIRS for p in low[:-1]) or low[-1] in JUNK_NAMES or (info.is_dir() and low[-1] in JUNK_DIRS):
                continue
            declared += info.file_size
            if declared > MAX_UNPACKED:
                raise AppError(f"O conteúdo descompactado passa de {MAX_UNPACKED // (1024 * 1024)} MB.", 413)
            cleaned.append((info, path))
        files = [p for i, p in cleaned if not i.is_dir()]
        if not files:
            raise AppError("O ZIP está vazio.")
        # Uma única pasta por fora de tudo (comum ao compactar a pasta do projeto): é retirada.
        tops = {p.parts[0] for p in files}
        if len(tops) == 1 and all(len(p.parts) > 1 for p in files):
            cleaned = [(i, PurePosixPath(*p.parts[1:])) for i, p in cleaned if len(p.parts) > 1]
        return cleaned

    # ----- troca de versões (chamadas pelo supervisor, com o processo parado)
    def swap_in_staged(self, name: str) -> None:
        folder = self.folder(name)
        if not (folder / "novo").is_dir():
            raise AppError("Não há código novo enviado para implantar.", 409)
        previous = folder / "anterior"
        if previous.exists():
            shutil.rmtree(previous)
        if (folder / "atual").exists():
            os.replace(folder / "atual", previous)
        os.replace(folder / "novo", folder / "atual")

    def swap_back(self, name: str) -> None:
        """atual ↔ anterior (voltar versão, ou desfazer uma implantação que falhou)."""
        folder = self.folder(name)
        if not (folder / "anterior").is_dir():
            raise AppError("Não há versão anterior guardada.", 409)
        temp = folder / ".troca"
        if temp.exists():
            shutil.rmtree(temp)
        if (folder / "atual").exists():
            os.replace(folder / "atual", temp)
        os.replace(folder / "anterior", folder / "atual")
        if temp.exists():
            os.replace(temp, folder / "anterior")

    def remove(self, name: str) -> dict:
        """Guarda em data/removidos/apps/ (como os sites); nunca apaga sozinho."""
        folder = self.folder(name)
        if not folder.is_dir():
            raise AppError("Servidor não encontrado.", 404)
        target_root = self.data / "removidos" / "apps"
        ensure_private_dir(target_root)
        rid = f"{int(self._clock())}-{name}"
        os.replace(folder, target_root / rid)
        return {"id": rid, "name": name}

    # ----- registro (saída do programa)
    def log_tail(self, name: str, lines: int = 200) -> list[str]:
        path = self.folder(name) / "logs" / "saida.log"
        lines = max(1, min(int(lines), 1000))
        try:
            with open(path, "rb") as handle:
                handle.seek(0, os.SEEK_END)
                size = handle.tell()
                handle.seek(max(0, size - 256 * 1024))
                data = handle.read().decode("utf-8", "replace")
        except OSError:
            return []
        return data.splitlines()[-lines:]

    # ----- estado dos processos (gravado pelo supervisor)
    def runtime(self) -> dict:
        info = read_json(self.state_file, default={})
        return info if isinstance(info, dict) else {}

    def disk(self) -> dict:
        out = {}
        for cfg in self.list():
            folder = self.root / cfg["name"]
            out[cfg["name"]] = {part: tree_size(folder / part)[0] for part in ("atual", "anterior", "dados", "logs")}
        return out


# =============================================================================================== processos (supervisor)
def _proc_stats(pid: int) -> dict:
    """Memória (RSS) e tempo de CPU do processo, lidos de /proc (funciona no Android/Termux, sem bibliotecas)."""
    out = {"rss_kb": None, "cpu_s": None}
    try:
        with open(f"/proc/{pid}/status", encoding="ascii", errors="replace") as handle:
            for line in handle:
                if line.startswith("VmRSS:"):
                    out["rss_kb"] = int(line.split()[1])
                    break
        with open(f"/proc/{pid}/stat", encoding="ascii", errors="replace") as handle:
            fields = handle.read().rsplit(")", 1)[1].split()
        ticks = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100
        out["cpu_s"] = round((int(fields[11]) + int(fields[12])) / ticks, 2)
    except (OSError, ValueError, IndexError):
        pass
    return out


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def _probe(port: int, path: str, timeout: float = 2.0) -> tuple[bool, str]:
    if not path:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=timeout):
                return True, ""
        except OSError:
            return False, "a porta não responde"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers={"User-Agent": "cdbs-supervisor"}), timeout=timeout) as resp:
            return 200 <= resp.status < 400, f"HTTP {resp.status}"
    except urllib.error.HTTPError as error:
        return False, f"HTTP {error.code} em {path}"
    except (OSError, ValueError, urllib.error.URLError):
        return False, "a porta não responde"


class AppProcess:
    """Um servidor rodando: saída para o registro (com rotação) e parada do GRUPO de processos (filhos juntos)."""

    def __init__(self, argv: list[str], *, cwd: Path, env: dict, log_path: Path, scrubber: Callable[[str, int], str] = scrub):
        self.log_path = log_path
        self.lines: list[str] = []
        self._scrub = scrubber
        self.started_at = time.time()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self.proc = subprocess.Popen(argv, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                     start_new_session=True)
        self._reader = threading.Thread(target=self._read, name="cdbs-app-saida", daemon=True)
        self._reader.start()

    @property
    def pid(self) -> int:
        return self.proc.pid

    def _rotate(self) -> None:
        for n in range(LOG_KEEP, 0, -1):
            older = self.log_path.with_name(f"{self.log_path.name}.{n}")
            newer = self.log_path.with_name(f"{self.log_path.name}.{n - 1}") if n > 1 else self.log_path
            if newer.exists():
                os.replace(newer, older)

    def _open_log(self):
        try:
            if self.log_path.exists() and self.log_path.stat().st_size > LOG_MAX:
                self._rotate()
            handle = open(self.log_path, "a", encoding="utf-8")
            os.chmod(self.log_path, 0o600)
            return handle
        except OSError:
            return None

    def _read(self) -> None:
        assert self.proc.stdout is not None
        log = self._open_log()
        if log is not None:
            log.write(f"──── {fmt_local(time.time())} iniciado (pid {self.proc.pid}) ────\n")
            log.flush()
        written = 0
        for raw in iter(self.proc.stdout.readline, b""):
            line = self._scrub(raw.decode("utf-8", "replace").rstrip("\r\n"), 2000)
            self.lines = (self.lines + [line])[-40:]
            if log is not None:
                try:
                    log.write(line + "\n")
                    log.flush()
                    written += len(line) + 1
                    if written > 64 * 1024 and self.log_path.stat().st_size > LOG_MAX:
                        log.close()
                        self._rotate()
                        log = open(self.log_path, "a", encoding="utf-8")
                        written = 0
                except OSError:
                    pass
        if log is not None:
            try:
                log.write(f"──── {fmt_local(time.time())} terminou (código {self.proc.poll()}) ────\n")
                log.close()
            except OSError:
                pass
        try:
            self.proc.stdout.close()
        except OSError:
            pass

    def alive(self) -> bool:
        return self.proc.poll() is None

    def stop(self, timeout: float = 8.0) -> int | None:
        if self.proc.poll() is None:
            try:
                os.killpg(self.proc.pid, signal.SIGTERM)
            except OSError:
                pass
            try:
                self.proc.wait(timeout)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(self.proc.pid, signal.SIGKILL)
                except OSError:
                    pass
                try:
                    self.proc.wait(5)
                except subprocess.TimeoutExpired:
                    pass
        else:
            try:
                os.killpg(self.proc.pid, signal.SIGKILL)       # sobrou algum filho do grupo? encerra também
            except OSError:
                pass
        self._reader.join(timeout=2)
        return self.proc.returncode

    def last_reason(self) -> str:
        useful = [l.strip() for l in self.lines if l.strip() and not l.startswith("──── ")]
        for line in reversed(useful):
            if not line.startswith(("File ", "^", "~", "at ")) and line != "Traceback (most recent call last):":
                return line[:240]
        return ""


class AppRunner:
    """Usado SÓ pelo supervisor. tick() é chamado no laço principal (a cada ~0,5 s)."""

    def __init__(self, store: AppStore, *, record: Callable[..., None] = lambda *a, **k: None, say: Callable[[str], None] = print,
                 python: str | None = None, node: str | None = None, restart_delays: tuple = RESTART_DELAYS,
                 max_crashes: int = MAX_CRASHES, crash_window: float = CRASH_WINDOW, health_every: float = HEALTH_EVERY,
                 health_fails: int = HEALTH_FAILS, start_grace: float = START_GRACE, reserved_ports: set[int] | None = None):
        self.store = store
        self.record, self.say = record, say
        self.python = python or sys.executable
        self.node = node if node is not None else (os.environ.get("CDBS_NODE_BIN") or shutil.which("node") or "")
        self.restart_delays, self.max_crashes, self.crash_window = restart_delays, max_crashes, crash_window
        self.health_every, self.health_fails, self.start_grace = health_every, health_fails, start_grace
        self.reserved_ports = reserved_ports or set()
        self.procs: dict[str, AppProcess] = {}
        self.rt: dict[str, dict] = {}
        self._cpu_last: dict[str, tuple[float, float]] = {}

    # ----- estado por servidor
    def _rt(self, name: str) -> dict:
        return self.rt.setdefault(name, {"state": "parado", "restarts": [], "failures": 0, "next_try_at": None, "health_fail": 0,
                                         "healthy_at": None, "started_at": None, "last_exit": None, "message": "", "last_health": 0.0,
                                         "starts": 0})

    def runtime_view(self) -> dict:
        out = {}
        configs = {c["name"]: c for c in self.store.list()}
        for name, cfg in configs.items():
            rt = self._rt(name)
            proc = self.procs.get(name)
            pid = proc.pid if proc and proc.alive() else None
            stats = _proc_stats(pid) if pid else {"rss_kb": None, "cpu_s": None}
            cpu_pct = None
            if pid and stats["cpu_s"] is not None:
                now = time.monotonic()
                last = self._cpu_last.get(name)
                if last and now - last[0] > 0.5:
                    cpu_pct = round(max(0.0, (stats["cpu_s"] - last[1]) / (now - last[0]) * 100), 1)
                self._cpu_last[name] = (now, stats["cpu_s"])
            out[name] = {"state": rt["state"] if cfg["has_code"] or rt["state"] != "parado" else "sem_codigo",
                         "pid": pid, "port": cfg["port"], "started_at": rt["started_at"], "healthy_at": rt["healthy_at"],
                         "restarts_recent": len(rt["restarts"]), "failures": rt["failures"], "next_try_at": rt["next_try_at"],
                         "last_exit": rt["last_exit"], "message": rt["message"], "rss_kb": stats["rss_kb"], "cpu_s": stats["cpu_s"],
                         "cpu_pct": cpu_pct, "enabled": cfg["enabled"]}
        return out

    # ----- iniciar / parar
    def _argv(self, cfg: dict) -> list[str]:
        if cfg["runtime"] == "python":
            return [self.python, "-u", cfg["entry"]]
        if not self.node:
            raise AppError("Node.js não está instalado. No Termux: pkg install nodejs -y", 409)
        return [self.node, cfg["entry"]]

    def _env(self, cfg: dict) -> dict:
        base = {k: os.environ[k] for k in ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "PREFIX", "TERMUX_VERSION", "ANDROID_ROOT", "ANDROID_DATA",
                                           "SSL_CERT_FILE") if k in os.environ}
        folder = self.store.root / cfg["name"]
        env = {**base, **self.store.env_for_process(cfg["name"])}
        env.update({"PORT": str(cfg["port"]), "HOST": "127.0.0.1", "CDBS_APP_NAME": cfg["name"], "CDBS_BASE_PATH": f"/{cfg['name']}",
                    "CDBS_APP_DATA": str(folder / "dados"), "PYTHONUNBUFFERED": "1", "NODE_ENV": env.get("NODE_ENV", "production")})
        return env

    def start(self, name: str) -> tuple[bool, str]:
        cfg = self.store.get(name)
        rt = self._rt(name)
        if name in self.procs and self.procs[name].alive():
            return True, "já está rodando"
        if not cfg["has_code"]:
            rt.update(state="parado", message="sem código: envie o ZIP e implante")
            return False, "Este servidor ainda não tem código. Envie o ZIP e implante."
        port = cfg["port"]
        if port in self.reserved_ports:
            rt.update(state="parado_por_falhas", message=f"a porta {port} é usada pelo próprio CDBS; ajuste CDBS_APP_PORT_BASE")
            return False, rt["message"]
        if not _port_free(port):
            rt.update(state="reiniciando" if rt["state"] in ("reiniciando",) else rt["state"], message=f"a porta {port} está ocupada por outro programa")
            return False, f"A porta {port} está ocupada por outro programa (sobrou algum processo?)."
        try:
            argv = self._argv(cfg)
        except AppError as exc:
            rt.update(state="parado_por_falhas", message=str(exc))
            return False, str(exc)
        folder = self.store.root / name
        self.procs[name] = AppProcess(argv, cwd=folder / "atual", env=self._env(cfg), log_path=folder / "logs" / "saida.log")
        rt.update(state="iniciando", started_at=int(time.time()), health_fail=0, healthy_at=None, next_try_at=None, message="", last_health=0.0)
        rt["starts"] += 1
        return True, "iniciando"

    def stop(self, name: str, reason: str = "") -> None:
        proc = self.procs.pop(name, None)
        if proc is not None:
            proc.stop()
        self._rt(name).update(state="parado", message=reason, next_try_at=None)

    def stop_all(self, reason: str = "") -> None:
        for name in list(self.procs):
            self.stop(name, reason)

    def healthy(self, name: str) -> tuple[bool, str]:
        cfg = self.store.get(name)
        return _probe(cfg["port"], cfg.get("health_path") or "")

    def _failed(self, name: str, reason: str) -> None:
        rt = self._rt(name)
        now = time.time()
        rt["restarts"] = [t for t in rt["restarts"] if now - t < self.crash_window] + [now]
        rt["failures"] += 1
        if len(rt["restarts"]) >= self.max_crashes:
            rt.update(state="parado_por_falhas", next_try_at=None,
                      message=f"{len(rt['restarts'])} falhas em {int(self.crash_window // 60)} min. Último motivo: {reason or 'sem detalhe'}")
            self.say(f"servidor {name}: falhou {len(rt['restarts'])} vezes seguidas; deixei parado. Motivo: {reason or '—'}")
            self.record("servidores", "app_parado_por_falhas", "falha", target=name, detail=rt["message"])
        else:
            delay = self.restart_delays[min(rt["failures"] - 1, len(self.restart_delays) - 1)]
            rt.update(state="reiniciando", next_try_at=now + delay, message=reason)
            self.say(f"servidor {name}: parou ({reason or 'sem detalhe'}); nova tentativa em {delay} s")
            self.record("servidores", "app_caiu", "falha", target=name, detail=f"{reason or 'sem detalhe'}; nova tentativa em {delay} s")

    # ----- vigilância (laço do supervisor)
    def tick(self, only: str | None = None, skip: str | None = None) -> None:
        try:
            configs = {c["name"]: c for c in self.store.list()}
        except Exception:                                                    # noqa: BLE001
            return
        for name in list(self.procs):
            if name not in configs:                                          # removido do disco por fora
                self.stop(name, "servidor removido")
        for name, cfg in configs.items():
            if (only and name != only) or (skip and name == skip):
                continue
            try:
                self._tick_one(name, cfg)
            except Exception as exc:                                         # noqa: BLE001 — um servidor nunca derruba os outros
                self._rt(name)["message"] = f"erro interno ignorado ({type(exc).__name__})"

    def _tick_one(self, name: str, cfg: dict) -> None:
        rt = self._rt(name)
        now = time.time()
        proc = self.procs.get(name)
        if rt["state"] == "implantando":
            return
        if not cfg["enabled"]:
            if proc is not None:
                self.stop(name, "desligado")
            elif rt["state"] not in ("parado",):
                rt.update(state="parado", next_try_at=None)
            return
        if rt["state"] == "parado_por_falhas":
            return
        if proc is None:
            if rt["state"] == "reiniciando" and rt["next_try_at"] and now < rt["next_try_at"]:
                return
            ok, why = self.start(name)
            if not ok and rt["state"] not in ("parado_por_falhas",):
                if "ocupada" in why:
                    self._failed(name, why)
                elif not cfg["has_code"]:
                    rt["state"] = "parado"
            return
        if not proc.alive():
            code = proc.proc.returncode
            reason = proc.last_reason() or f"saiu com código {code}"
            self.procs.pop(name, None)
            proc.stop()
            rt["last_exit"] = {"code": code, "at": int(now), "reason": reason}
            self._failed(name, reason)
            return
        starting = rt["state"] == "iniciando"
        every = 1.0 if starting else self.health_every
        if now - rt["last_health"] < every:
            return
        rt["last_health"] = now
        ok, why = self.healthy(name)
        if ok:
            if rt["state"] != "rodando":
                self.say(f"servidor {name} no ar (127.0.0.1:{cfg['port']}, público em /{name}/)")
                if rt["failures"] or rt["starts"] > 1:
                    self.record("servidores", "app_no_ar", "ok", target=name, detail=f"porta {cfg['port']}")
            rt.update(state="rodando", health_fail=0, healthy_at=int(now), message="")
            if rt["failures"] and now - (rt["started_at"] or now) > STABLE_AFTER:
                rt["failures"] = 0
            return
        if starting and now - (rt["started_at"] or now) < self.start_grace:
            rt["message"] = f"esperando responder ({why})"
            return
        rt["health_fail"] += 1
        if starting or rt["health_fail"] >= self.health_fails:
            reason = f"não respondeu à verificação de saúde ({why})" if starting else f"travado: {why} em {rt['health_fail']} verificações seguidas"
            self.procs.pop(name, None)
            proc.stop()
            rt["last_exit"] = {"code": proc.proc.returncode, "at": int(now), "reason": reason}
            self._failed(name, reason)

    def wait_healthy(self, name: str, timeout: float, pump: Callable[[], None] = lambda: None) -> tuple[bool, str]:
        """Espera o servidor responder (ou desistir). pump() mantém o resto do supervisor andando enquanto isso."""
        deadline = time.time() + timeout
        rt = self._rt(name)
        while time.time() < deadline:
            proc = self.procs.get(name)
            if proc is None or not proc.alive():
                reason = proc.last_reason() if proc else rt.get("message", "")
                return False, reason or "o programa terminou logo ao iniciar"
            ok, why = self.healthy(name)
            if ok:
                rt.update(state="rodando", health_fail=0, healthy_at=int(time.time()), message="")
                return True, ""
            rt["message"] = f"esperando responder ({why})"
            pump()
            time.sleep(0.4)
        return False, f"não respondeu em {int(timeout)} s ({rt.get('message', '')})"

    # ----- operações pedidas pelo painel (chamadas pelo supervisor, uma de cada vez)
    def op_start(self, name: str, pump: Callable[[], None]) -> tuple[str, str]:
        cfg = self.store.get(name)
        if not cfg["has_code"]:
            return "falhou", "Este servidor ainda não tem código. Envie o ZIP e implante."
        self.store.set_enabled(name, True)
        rt = self._rt(name)
        rt.update(failures=0, restarts=[])
        if name in self.procs and self.procs[name].alive() and rt["state"] == "rodando":
            return "ok", "O servidor já está rodando."
        if name in self.procs:
            self.stop(name, "reinício pedido")
        ok, why = self.start(name)
        if not ok:
            return "falhou", why
        healthy, why = self.wait_healthy(name, self.start_grace, pump)
        if healthy:
            self.record("servidores", "app_ligado", "ok", target=name)
            return "ok", f"Servidor {name} no ar."
        self.stop(name, why)
        self.store.set_enabled(name, False)
        self.record("servidores", "app_ligado", "falha", target=name, detail=why)
        return "falhou", f"O servidor {name} não respondeu: {why}. Veja o registro. Ele foi deixado desligado."

    def op_stop(self, name: str) -> tuple[str, str]:
        self.store.get(name)
        self.store.set_enabled(name, False)
        self.stop(name, "desligado a pedido")
        self.record("servidores", "app_desligado", "ok", target=name)
        return "ok", f"Servidor {name} desligado. O endereço /{name}/ responde \"fora do ar\" até ser ligado de novo."

    def op_deploy(self, name: str, pump: Callable[[], None], *, rollback: bool = False) -> tuple[str, str, dict]:
        """Implantação (ou volta de versão) com volta automática se a versão nova não responder."""
        cfg = self.store.get(name)
        if rollback and not cfg["has_previous"]:
            return "falhou", "Não há versão anterior guardada.", {}
        if not rollback and not cfg["has_staged"]:
            return "falhou", "Não há código novo enviado. Envie o ZIP primeiro.", {}
        rt = self._rt(name)
        had_code = cfg["has_code"]
        self.stop(name, "implantação" if not rollback else "voltando versão")
        rt["state"] = "implantando"
        try:
            if rollback:
                self.store.swap_back(name)
            else:
                self.store.swap_in_staged(name)
        except (AppError, OSError) as exc:
            rt["state"] = "parado"
            return "falhou", f"Não consegui trocar os arquivos: {exc}. Nada foi alterado no que estava rodando.", {}
        self.store.mark_deployed(name)
        self.store.set_enabled(name, True)
        rt.update(state="parado", failures=0, restarts=[])
        ok, why = self.start(name)
        healthy, why2 = self.wait_healthy(name, self.start_grace, pump) if ok else (False, why)
        what = "versão anterior recolocada" if rollback else "código novo implantado"
        if healthy:
            self.record("servidores", "app_versao_voltada" if rollback else "app_implantado", "ok", target=name)
            return "ok", f"Servidor {name}: {what} e respondendo.", {}
        reason = why2 or why
        self.stop(name, "voltando")
        if had_code:
            try:
                self.store.swap_back(name)                       # devolve exatamente o que estava antes
            except (AppError, OSError) as exc:
                self.record("servidores", "app_implantacao_falhou", "falha", target=name, detail=f"{reason}; volta falhou: {exc}")
                rt.update(state="parado_por_falhas", message=f"versão nova falhou e a volta também: {exc}")
                return "falhou", f"A versão nova não respondeu ({reason}) e não consegui voltar: {exc}.", {}
            self.start(name)
            back, _ = self.wait_healthy(name, self.start_grace, pump)
            msg = f"A versão nova não respondeu ({reason}). A versão anterior foi recolocada" + ("" if back else ", mas também não respondeu") + "."
            self.record("servidores", "app_implantacao_revertida", "falha", target=name, detail=msg)
            return "falhou", msg, {"reverted": back}
        self.store.set_enabled(name, False)
        rt.update(state="parado", message=reason)
        self.record("servidores", "app_implantacao_falhou", "falha", target=name, detail=reason)
        return "falhou", f"O código novo não respondeu ({reason}). Ele ficou instalado, mas desligado: corrija e envie de novo. Veja o registro.", {}

    def op_remove(self, name: str) -> tuple[str, str]:
        self.store.get(name)
        self.stop(name, "removido")
        info = self.store.remove(name)
        self.rt.pop(name, None)
        self.record("servidores", "app_removido", "ok", target=name, ref=info["id"])
        return "ok", f"Servidor {name} removido. Os arquivos foram guardados em data/removidos/apps/{info['id']}."

    # ----- ciclo de vida do supervisor
    def write_state(self) -> None:
        write_json_private(self.store.state_file, {"updated_at": int(time.time()), "supervisor_pid": os.getpid(), "apps": self.runtime_view()})

    def cleanup_orphans(self) -> list[int]:
        """Processos que sobraram de uma execução anterior do supervisor (ex.: o Android encerrou o Termux).
        Só encerra quem estiver rodando DENTRO da pasta do servidor (o PID pode ter sido reaproveitado pelo Android)."""
        killed = []
        old = self.store.runtime().get("apps") or {}
        for name, info in old.items():
            pid = (info or {}).get("pid")
            if not isinstance(pid, int) or pid <= 1 or pid == os.getpid() or not NAME_RE.fullmatch(str(name)):
                continue
            try:
                cwd = os.readlink(f"/proc/{pid}/cwd")
            except OSError:
                continue
            if not cwd.startswith(str(self.store.root / name)):
                continue
            try:
                os.killpg(pid, signal.SIGTERM)
            except OSError:
                continue
            for _ in range(40):
                time.sleep(0.1)
                if not _running(pid):
                    break
            else:
                try:
                    os.killpg(pid, signal.SIGKILL)
                except OSError:
                    pass
            killed.append(pid)
        return killed


def _running(pid: int) -> bool:
    """Vivo e não zumbi."""
    try:
        with open(f"/proc/{pid}/stat", encoding="ascii", errors="replace") as handle:
            return handle.read().rsplit(")", 1)[1].split()[0] not in ("Z", "X")
    except (OSError, IndexError):
        return False


def doctor_checks(ctx) -> list:
    store = AppStore(ctx.data)
    apps = store.list()
    out = [("servidores", "vagas", "INFO", f"{len(apps)} de {APP_SLOTS} vagas usadas" + (f" ({', '.join(a['name'] for a in apps)})" if apps else ""))]
    rt = (store.runtime().get("apps") or {}) if apps else {}
    for app in apps:
        state = (rt.get(app["name"]) or {}).get("state", "desconhecido")
        status = "OK" if state in ("rodando",) or (state in ("parado", "sem_codigo") and not app["enabled"]) else "AVISO"
        msg = (rt.get(app["name"]) or {}).get("message") or ""
        out.append(("servidores", app["name"], status, f"{label(state)} · {RUNTIMES.get(app['runtime'], app['runtime'])} · porta {app['port']}" + (f" — {msg}" if msg else ""),
                    "Veja o registro no painel (Servidores) e reinicie depois de corrigir." if status != "OK" else ""))
    if any(a["runtime"] == "node" for a in apps) and not (os.environ.get("CDBS_NODE_BIN") or shutil.which("node")):
        out.append(("servidores", "node", "AVISO", "há servidor Node.js, mas o node não está instalado", "No Termux: pkg install nodejs -y"))
    return out
