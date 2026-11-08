#!/usr/bin/env python3
"""CDBS.OS — supervisor (o "guardião"), Partes 2.2, 2.5 e 2.6.

É o processo que o `start.sh` inicia. Ele NÃO serve páginas: ele cuida de quem serve.
  • Inicia o servidor do CDBS (cdbs.py) como processo filho e confere a saúde dele a cada 15 s (rota de saúde do
    painel + ping do servidor dos sites). Se o servidor cair ou travar, reinicia com espera crescente
    (2 s, 5 s, 15 s, 30 s, 1 min, 2 min, 5 min) e, depois de 6 falhas em 15 minutos, PARA de tentar e registra o motivo —
    nunca fica em laço de reinício.
  • Opcionalmente cuida do túnel Cloudflare (cloudflared): liga, desliga, reinicia, descobre o link público, percebe
    quando ele cai e volta — também com espera crescente e limite.
  • Executa, UMA POR VEZ, os pedidos da lista permitida que o painel e o bot deixam em data/system/control/
    (reiniciar servidor, ligar/desligar/reiniciar túnel, instalar atualização, voltar versão). Texto recebido nunca é
    executado como comando.
  • Instala atualizações com cópia de segurança, verificação de saúde e volta automática (updater.py). Como é outro
    processo, a atualização não depende do servidor que ela mesma substitui.
  • Ao iniciar, termina com segurança uma atualização que o Android tenha interrompido.

Uso:
    python3 supervisor.py run                 (o start.sh faz isso)
    python3 supervisor.py status
    python3 supervisor.py stop
    python3 supervisor.py restart             reinicia o servidor do CDBS
    python3 supervisor.py tunnel start|stop|restart|status
    python3 supervisor.py --selftest          confere se este código carrega (usado após uma atualização)
"""
from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import apps as apps_mod     # noqa: E402
import audit                # noqa: E402
import config               # noqa: E402
import control              # noqa: E402
import lifecycle            # noqa: E402
import settings as settings_mod   # noqa: E402
import updater as updater_mod     # noqa: E402
import versioninfo          # noqa: E402
from sysutil import FileLock, fmt_local, read_json, scrub, write_json_private   # noqa: E402

EXIT_CANNOT_START = 3
RESTART_DELAYS = (2, 5, 15, 30, 60, 120, 300)
CRASH_WINDOW, MAX_CRASHES, STABLE_AFTER = 900, 6, 600
HEALTH_EVERY, HEALTH_FAILS, START_GRACE = 15.0, 3, 25.0
TUNNEL_DELAYS = (5, 15, 30, 60, 120, 300)
TUNNEL_WINDOW, TUNNEL_MAX = 1800, 8
TUNNEL_LOST_AFTER, TUNNEL_STALE_RESTART = 120.0, 900.0
URL_RE = re.compile(r"https://[-a-z0-9]+\.trycloudflare\.com")


def http_status(url: str, timeout: float = 2.0) -> tuple[int, bytes]:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(urllib.request.Request(url, headers={"User-Agent": "cdbs-supervisor", "Host": url.split("/")[2]}), timeout=timeout) as response:
            return response.status, response.read(4096)
    except urllib.error.HTTPError as error:
        return error.code, b""
    except (OSError, ValueError, urllib.error.URLError):
        return 0, b""


class Child:
    """Processo filho com a saída repassada para o terminal (e as últimas linhas guardadas para explicar falhas)."""

    def __init__(self, name: str, argv: list[str], *, cwd: Path, env: dict, echo: Callable[[str], bool], on_line: Callable[[str], None] | None = None,
                 log_path: Path | None = None):
        self.name = name
        self.lines: deque[str] = deque(maxlen=40)
        self._echo, self._on_line, self._log_path = echo, on_line, log_path
        self.started_at = time.time()
        self.proc = subprocess.Popen(argv, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                     text=True, bufsize=1, errors="replace", start_new_session=True)   # Ctrl+C do terminal chega só ao supervisor
        self._reader = threading.Thread(target=self._read, name=f"cdbs-{name}-saida", daemon=True)
        self._reader.start()

    @property
    def pid(self) -> int:
        return self.proc.pid

    def _read(self) -> None:
        assert self.proc.stdout is not None
        log = None
        if self._log_path is not None:
            try:
                self._log_path.parent.mkdir(parents=True, exist_ok=True)
                if self._log_path.exists() and self._log_path.stat().st_size > 256 * 1024:
                    self._log_path.replace(self._log_path.with_suffix(".log.1"))
                log = open(self._log_path, "a", encoding="utf-8")
            except OSError:
                log = None
        for raw in self.proc.stdout:
            line = raw.rstrip("\n")
            self.lines.append(scrub(line, 300))
            if log is not None:
                try:
                    log.write(scrub(line, 500) + "\n")
                    log.flush()
                except OSError:
                    pass
            if self._on_line is not None:
                try:
                    self._on_line(line)
                except Exception:                                    # noqa: BLE001
                    pass
            if self._echo(line):
                print(line, flush=True)
        if log is not None:
            log.close()
        try:
            self.proc.stdout.close()
        except OSError:
            pass

    def alive(self) -> bool:
        return self.proc.poll() is None

    def stop(self, timeout: float = 10.0) -> int | None:
        if self.proc.poll() is None:
            try:
                self.proc.terminate()
                self.proc.wait(timeout)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                try:
                    self.proc.wait(5)
                except subprocess.TimeoutExpired:
                    pass
            except OSError:
                pass
        self._reader.join(timeout=2)
        return self.proc.returncode

    def last_reason(self) -> str:
        """A linha que melhor explica a saída: a mensagem 'não iniciou' do cdbs.py ou, senão, a última linha útil."""
        lines = [line.strip() for line in self.lines if line.strip()]
        for line in reversed(lines):
            if "não iniciou" in line:
                return line[:240]
        for line in reversed(lines):
            if not line.startswith(("File ", "^", "~")) and line != "Traceback (most recent call last):":
                return line[:240]
        return ""


class Supervisor:
    def __init__(self, root: Path = ROOT, *, server_argv: list[str] | None = None, cloudflared: str | None = None,
                 health_every: float = HEALTH_EVERY, health_fails: int = HEALTH_FAILS, start_grace: float = START_GRACE,
                 restart_delays: tuple = RESTART_DELAYS, max_crashes: int = MAX_CRASHES, crash_window: float = CRASH_WINDOW,
                 tunnel_delays: tuple = TUNNEL_DELAYS, tunnel_max: int = TUNNEL_MAX, tunnel_lost_after: float = TUNNEL_LOST_AFTER,
                 echo_server: bool = True, poll: float = 0.5, health_timeout: float = 45.0, ready_every: float = 5.0):
        self.root = root
        self.data = root / "data"
        self.system = self.data / "system"
        values, _warnings = config.parse_env(root / ".env")
        # Mesma regra do cdbs.py (o ambiente vence o .env), sem alterar o ambiente deste processo.
        self.cfg = {**values, **{k: os.environ[k] for k in config.KNOWN_KEYS if os.environ.get(k, "").strip()}}
        self.site_port = self._port("CDBS_SITE_PORT", 8088)
        self.admin_port = self._port("CDBS_ADMIN_PORT", 8089)
        self.metrics_port = self._port("CDBS_TUNNEL_METRICS_PORT", 20241)
        self.ready_every = ready_every
        self.server_argv = server_argv or [sys.executable, "-u", str(root / "cdbs.py"), "serve"]
        self.cloudflared = cloudflared or os.environ.get("CDBS_CLOUDFLARED_BIN") or shutil.which("cloudflared") or ""
        self.health_every, self.health_fails, self.start_grace = health_every, health_fails, start_grace
        self.restart_delays, self.max_crashes, self.crash_window = restart_delays, max_crashes, crash_window
        self.tunnel_delays, self.tunnel_max, self.tunnel_lost_after = tunnel_delays, tunnel_max, tunnel_lost_after
        self.echo_server, self.poll, self.health_timeout = echo_server, poll, health_timeout
        self.audit = audit.AuditLog(self.system)
        self.settings = settings_mod.Settings(self.system, environ=self.cfg)
        self.updater = updater_mod.Updater(root, self.data)
        self._stop = threading.Event()
        self.server: Child | None = None
        self.tunnel: Child | None = None
        self.state = {"server": {"state": "parado", "restarts": [], "failures": 0, "last_exit": None, "next_try_at": None, "health_fail": 0,
                                 "healthy_at": None, "started_at": None, "message": ""},
                      "tunnel": {"state": "desligado", "mode": "quick", "url": "", "url_since": None, "restarts": [], "failures": 0,
                                 "next_try_at": None, "ready_at": None, "lost_since": None, "message": "", "wanted": False}}
        self._last_health = 0.0
        self._last_ready_check = 0.0
        self._last_state_write = 0.0
        self._beat = lifecycle.Heartbeat(self.system / "supervisor-heartbeat.json")
        self._lock = FileLock(self.system / "supervisor.lock")
        self._busy = ""
        self._started_at = int(time.time())
        # 4.0: servidores (aplicativos) — iniciados e vigiados aqui, como o servidor do CDBS
        self.app_store = apps_mod.AppStore(self.data, environ=self.cfg)
        self.apps = apps_mod.AppRunner(self.app_store, record=self.record, say=self.say,
                                       reserved_ports={self.site_port, self.admin_port, self.metrics_port})

    def _port(self, name: str, default: int) -> int:
        try:
            value = int(self.cfg.get(name, "") or default)
        except ValueError:
            return default
        return value if 1024 <= value <= 65535 else default

    def say(self, text: str) -> None:
        print(f"[guardião] {text}", flush=True)

    def record(self, category: str, action: str, result: str = "ok", **fields: str) -> None:
        try:
            self.audit.record(category, action, result, actor="supervisor", **fields)
        except Exception:                                            # noqa: BLE001
            pass

    # ----------------------------------------------------------------- estado gravado (painel, diagnóstico, atualizar.sh)
    def write_state(self, force: bool = False) -> None:
        now = time.time()
        if not force and now - self._last_state_write < 5:
            return
        self._last_state_write = now
        server, tunnel = self.state["server"], self.state["tunnel"]
        info = {"pid": os.getpid(), "started_at": self._started_at, "heartbeat_at": int(now), "version": versioninfo.load(self.root)["version"],
                "busy": self._busy, "features": ["apps"], "app_slots": apps_mod.APP_SLOTS,
                "server": {k: server[k] for k in ("state", "failures", "last_exit", "next_try_at", "healthy_at", "started_at", "message")}
                | {"pid": self.server.pid if self.server and self.server.alive() else None, "restarts_recent": len(server["restarts"])},
                "tunnel": {k: tunnel[k] for k in ("state", "mode", "url", "url_since", "ready_at", "message", "failures")}
                | {"pid": self.tunnel.pid if self.tunnel and self.tunnel.alive() else None}}
        write_json_private(self.system / "supervisor.json", info)
        try:
            self.apps.write_state()
        except Exception:                                                    # noqa: BLE001
            pass
        write_json_private(self.system / "tunnel.json", {**info["tunnel"], "managed": True, "updated_at": int(now),
                                                         "metrics_port": self.metrics_port})

    # ----------------------------------------------------------------- servidor
    def _server_env(self) -> dict:
        env = dict(os.environ)
        env["CDBS_SUPERVISED"] = "1"
        env["CDBS_SUPERVISOR_PID"] = str(os.getpid())
        env["PYTHONUNBUFFERED"] = "1"
        return env

    def start_server(self) -> None:
        server = self.state["server"]
        self.server = Child("servidor", self.server_argv, cwd=self.root, env=self._server_env(), echo=lambda _l: self.echo_server)
        server.update(state="iniciando", started_at=int(time.time()), health_fail=0, healthy_at=None, next_try_at=None, message="")
        self._last_health = time.time()
        self.write_state(force=True)

    def stop_server(self, reason: str = "") -> None:
        if self.server is not None:
            self.state["server"]["state"] = "parando"
            self.server.stop()
            self.server = None
        self.state["server"].update(state="parado", message=reason)
        self.write_state(force=True)

    def _server_failed(self, reason: str, cannot_start: bool) -> None:
        server = self.state["server"]
        now = time.time()
        server["restarts"] = [t for t in server["restarts"] if now - t < self.crash_window] + [now]
        server["failures"] += 1
        if len(server["restarts"]) >= self.max_crashes:
            server.update(state="parado_por_falhas", next_try_at=None,
                          message=f"{len(server['restarts'])} falhas em {int(self.crash_window // 60)} min. Último motivo: {reason or 'sem detalhe'}")
            self.say(f"o servidor falhou {len(server['restarts'])} vezes seguidas; NÃO vou tentar de novo sozinho. Motivo: {reason or '—'}")
            self.say("Corrija o problema (python3 cdbs.py doctor) e reinicie com: python3 supervisor.py restart  (ou Ctrl+C e bash start.sh)")
            self.record("servico", "servidor_parado_por_falhas", "falha", detail=server["message"])
        else:
            delay = self.restart_delays[min(server["failures"] - 1, len(self.restart_delays) - 1)]
            server.update(state="reiniciando", next_try_at=now + delay, message=reason)
            self.say(f"o servidor {'não conseguiu iniciar' if cannot_start else 'parou'} ({reason or 'sem detalhe'}); nova tentativa em {delay} s")
            self.record("servico", "inicio_falhou" if cannot_start else "servidor_reiniciado", "falha",
                        detail=f"{reason or 'sem detalhe'}; nova tentativa em {delay} s")
        self.write_state(force=True)

    def watch_server(self) -> None:
        server = self.state["server"]
        now = time.time()
        if server["state"] == "reiniciando" and server["next_try_at"] and now >= server["next_try_at"]:
            self.start_server()
            return
        if self.server is None or server["state"] in ("parado", "parado_por_falhas", "parando", "atualizando"):
            return
        if not self.server.alive():
            code = self.server.proc.returncode
            reason = self.server.last_reason()
            server["last_exit"] = {"code": code, "at": int(now), "reason": reason}
            self.server = None
            self._server_failed(reason or f"saiu com código {code}", cannot_start=code == EXIT_CANNOT_START)
            return
        # Enquanto o servidor está subindo (dentro do prazo de início), confere a cada 1 s: assim "no ar" aparece assim que
        # ele responde, e não até 15 s depois. Passado o prazo, volta ao ritmo normal (a contagem de falhas não muda).
        starting = server["state"] == "iniciando" and now - (server["started_at"] or now) < self.start_grace
        if now - self._last_health < (min(1.0, self.health_every) if starting else self.health_every):
            return
        self._last_health = now
        if server["state"] == "iniciando" and now - server["started_at"] < self.start_grace and not self.healthy():
            return
        if self.healthy():
            changed = server["state"] != "rodando"
            if changed:
                self.say(f"servidor no ar (painel 127.0.0.1:{self.admin_port}, sites 127.0.0.1:{self.site_port})")
            server.update(state="rodando", health_fail=0, healthy_at=int(now))
            if changed:
                self.write_state(force=True)                                   # painel e "status" veem "rodando" na hora
            if server["failures"] and now - (server["started_at"] or now) > STABLE_AFTER:
                server["failures"] = 0                                         # ficou estável: a espera volta ao começo
            return
        server["health_fail"] += 1
        if server["health_fail"] >= self.health_fails:
            self.say("o servidor parou de responder (travado); reiniciando")
            self.record("servico", "servidor_travado", "falha", detail=f"sem resposta em {server['health_fail']} verificações seguidas")
            child, self.server = self.server, None
            child.stop()
            server["last_exit"] = {"code": child.proc.returncode, "at": int(now), "reason": "travado (sem resposta)"}
            self._server_failed("travado (sem resposta às verificações de saúde)", cannot_start=False)

    def healthy(self, expect_version: str | None = None) -> bool:
        status, body = http_status(f"http://127.0.0.1:{self.admin_port}/api/health")
        if status != 200:
            return False
        if expect_version is not None:
            try:
                import json as _json                                          # noqa: PLC0415
                if _json.loads(body).get("version") != expect_version:
                    return False
            except ValueError:
                return False
        return http_status(f"http://127.0.0.1:{self.site_port}/_cdbs/ping")[0] == 200

    def wait_healthy(self, timeout: float, expect_version: str | None = None) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.server is None or not self.server.alive():
                return False
            if self.healthy(expect_version):
                self.state["server"].update(state="rodando", health_fail=0, healthy_at=int(time.time()))
                return True
            self._pump_tunnel()
            self.write_state()                                                 # o sinal de vida continua durante esperas longas
            time.sleep(0.5)
        return False

    # ----------------------------------------------------------------- túnel
    def _tunnel_mode(self) -> str:
        return self.settings.get()["tunnel"]["mode"]

    def _tunnel_echo(self, line: str) -> bool:
        # O cloudflared escreve muito (INF). No terminal só aparecem os erros; o link é anunciado pelo próprio guardião.
        return " ERR " in line and not URL_RE.search(line)

    def _on_tunnel_line(self, line: str) -> None:
        match = URL_RE.search(line)
        tunnel = self.state["tunnel"]
        if match and match.group(0) != tunnel["url"]:
            tunnel.update(url=match.group(0), url_since=int(time.time()))
            self.say(f"link público: {match.group(0)}   (para um site: {match.group(0)}/NOME/)")
            self.record("tunel", "tunel_link_novo", "info", detail=match.group(0))
            self.write_state(force=True)

    def start_tunnel(self, by: str = "supervisor") -> tuple[bool, str]:
        tunnel = self.state["tunnel"]
        mode = self._tunnel_mode()
        tunnel["mode"] = mode
        if mode == "off":
            tunnel.update(state="desligado", message="túnel desativado nas configurações (modo off)")
            return False, tunnel["message"]
        if not self.cloudflared or not (os.path.isfile(self.cloudflared) or shutil.which(self.cloudflared)):
            tunnel.update(state="indisponivel", message="cloudflared não está instalado. No Termux: pkg install cloudflared -y")
            self.write_state(force=True)
            return False, tunnel["message"]
        env = dict(os.environ)
        argv = [self.cloudflared, "tunnel", "--no-autoupdate", "--metrics", f"127.0.0.1:{self.metrics_port}"]
        if mode == "named":
            token = self.settings.secret("tunnel_token")
            if not token:
                tunnel.update(state="desligado", message="modo named sem token do túnel (CDBS_TUNNEL_TOKEN)")
                self.write_state(force=True)
                return False, tunnel["message"]
            env["TUNNEL_TOKEN"] = token                                         # pelo ambiente: o token não aparece na lista de processos
            argv.append("run")
        else:
            argv += ["--url", f"http://127.0.0.1:{self.site_port}"]               # SÓ a porta dos sites; o painel (8089) nunca
        if mode == "quick":
            tunnel["url"] = ""
        self.tunnel = Child("tunel", argv, cwd=self.root, env=env, echo=self._tunnel_echo, on_line=self._on_tunnel_line,
                            log_path=self.system / "tunnel.log")
        tunnel.update(state="iniciando", wanted=True, ready_at=None, lost_since=None, next_try_at=None, message="")
        self._last_ready_check = 0.0
        self.write_state(force=True)
        self.say(f"túnel iniciando ({'endereço fixo' if mode == 'named' else 'Quick Tunnel, endereço temporário'})")
        return True, "iniciando"

    def stop_tunnel(self, reason: str = "") -> None:
        tunnel = self.state["tunnel"]
        tunnel["wanted"] = False
        if self.tunnel is not None:
            self.tunnel.stop()
            self.tunnel = None
        tunnel.update(state="desligado", ready_at=None, lost_since=None, next_try_at=None, message=reason, url="" if tunnel["mode"] == "quick" else tunnel["url"])
        self.write_state(force=True)

    def _tunnel_failed(self, reason: str) -> None:
        tunnel = self.state["tunnel"]
        now = time.time()
        tunnel["restarts"] = [t for t in tunnel["restarts"] if now - t < TUNNEL_WINDOW] + [now]
        tunnel["failures"] += 1
        if len(tunnel["restarts"]) >= self.tunnel_max:
            tunnel.update(state="parado_por_falhas", next_try_at=None, message=f"falhou {len(tunnel['restarts'])} vezes em 30 min: {reason}")
            self.say(f"o túnel falhou várias vezes seguidas; deixei desligado. Motivo: {reason}")
            self.record("tunel", "tunel_parado_por_falhas", "falha", detail=tunnel["message"])
        else:
            delay = self.tunnel_delays[min(tunnel["failures"] - 1, len(self.tunnel_delays) - 1)]
            tunnel.update(state="reiniciando", next_try_at=now + delay, message=reason)
            self.record("tunel", "tunel_caiu", "falha", detail=f"{reason}; nova tentativa em {delay} s")
        self.write_state(force=True)

    def watch_tunnel(self) -> None:
        tunnel = self.state["tunnel"]
        now = time.time()
        if tunnel["state"] == "reiniciando" and tunnel["next_try_at"] and now >= tunnel["next_try_at"] and tunnel["wanted"]:
            self.start_tunnel()
            return
        if self.tunnel is None:
            return
        if not self.tunnel.alive():
            reason = self.tunnel.last_reason() or f"cloudflared saiu (código {self.tunnel.proc.returncode})"
            self.tunnel = None
            self._tunnel_failed(reason)
            return
        if now - self._last_ready_check < (min(1.0, self.ready_every) if tunnel["state"] == "iniciando" else self.ready_every):
            return
        self._last_ready_check = now
        status, _ = http_status(f"http://127.0.0.1:{self.metrics_port}/ready")
        if status == 200:
            if tunnel["state"] != "conectado":
                was_lost = tunnel["state"] == "desconectado"
                tunnel.update(state="conectado", ready_at=int(now), lost_since=None, message="")
                if tunnel["failures"] and now - (tunnel["restarts"][-1] if tunnel["restarts"] else now) > 600:
                    tunnel["failures"] = 0
                self.record("tunel", "tunel_conectado", "ok", detail=tunnel["url"] or "endereço fixo")
                if was_lost:
                    self.say("túnel conectado de novo")
                self.write_state(force=True)
            return
        if tunnel["state"] == "conectado":
            tunnel["lost_since"] = tunnel["lost_since"] or now
            if now - tunnel["lost_since"] >= self.tunnel_lost_after:
                tunnel.update(state="desconectado", message="sem conexão com a Cloudflare (internet caiu?); o cloudflared tenta reconectar sozinho")
                self.record("tunel", "tunel_caiu", "falha", detail=tunnel["message"])
                self.write_state(force=True)
        elif tunnel["state"] == "desconectado" and tunnel["lost_since"] and now - tunnel["lost_since"] > TUNNEL_STALE_RESTART:
            self.say("o túnel ficou desconectado por muito tempo; reiniciando o cloudflared")
            self.tunnel.stop()
            self.tunnel = None
            self._tunnel_failed("desconectado por mais de 15 minutos")

    def wait_tunnel(self, timeout: float) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.watch_tunnel()
            tunnel = self.state["tunnel"]
            if tunnel["state"] == "conectado" and (tunnel["url"] or tunnel["mode"] == "named"):
                return True
            if self.tunnel is None and tunnel["state"] != "iniciando":
                return False
            self.write_state()
            time.sleep(0.3)
        return False

    def _pump_tunnel(self) -> None:
        try:
            self.watch_tunnel()
        except Exception:                                                    # noqa: BLE001
            pass

    # ----------------------------------------------------------------- pedidos (uma operação por vez)
    def process_requests(self) -> None:
        for item in control.pending(self.data):
            rid, op = item["id"], item.get("op")
            control.take(self.data, rid)
            if op not in control.OPERATIONS:
                control.write_result(self.data, rid, str(op), "recusado", "Operação não permitida.")
                continue
            if control.is_expired(item):
                control.write_result(self.data, rid, op, "expirado", "Pedido antigo demais (o supervisor estava parado). Peça de novo.")
                continue
            control.write_result(self.data, rid, op, "em_andamento", control.OPERATIONS[op])
            self._busy = op
            self.write_state(force=True)
            try:
                status, message, extra = getattr(self, f"op_{op}")(item.get("args") or {}, item.get("by", "painel"))
            except updater_mod.UpdateError as exc:
                status, message, extra = "falhou", str(exc), {"code": exc.code}
            except Exception as exc:                                         # noqa: BLE001
                status, message, extra = "falhou", f"erro inesperado no supervisor ({type(exc).__name__})", {}
            finally:
                self._busy = ""
            control.write_result(self.data, rid, op, status, message, **extra)
            self.write_state(force=True)

    def op_server_restart(self, args: dict, by: str) -> tuple[str, str, dict]:
        self.say(f"reiniciando o servidor (pedido: {by})")
        self.stop_server("reinício pedido")
        self.state["server"].update(failures=0, restarts=[])
        self.start_server()
        ok = self.wait_healthy(self.health_timeout)
        self.record("servico", "servidor_reiniciado_a_pedido", "ok" if ok else "falha", detail=f"pedido por {by}")
        return ("ok", "Servidor reiniciado e respondendo.", {}) if ok else ("falhou", "O servidor foi reiniciado mas não respondeu a tempo.", {})

    def op_server_start(self, args: dict, by: str) -> tuple[str, str, dict]:
        if self.server is not None and self.server.alive():
            return "ok", "O servidor já está rodando.", {}
        return self.op_server_restart(args, by)

    def op_tunnel_start(self, args: dict, by: str) -> tuple[str, str, dict]:
        if self.tunnel is not None and self.tunnel.alive():
            tunnel = self.state["tunnel"]
            return "ok", "O túnel já estava ligado." + (f" Link: {tunnel['url']}" if tunnel["url"] else ""), {"url": tunnel["url"], "tunnel_state": tunnel["state"]}
        self.state["tunnel"].update(failures=0, restarts=[])
        started, why = self.start_tunnel(by)
        if not started:
            return "falhou", why, {}
        self.record("tunel", "tunel_ligado", "ok", detail=f"pedido por {by}")
        if self.wait_tunnel(float(args.get("wait", 45))):
            tunnel = self.state["tunnel"]
            return "ok", "Túnel conectado." + (f" Link: {tunnel['url']}" if tunnel["url"] else ""), {"url": tunnel["url"], "tunnel_state": "conectado"}
        tunnel = self.state["tunnel"]
        return "falhou", f"O túnel foi iniciado, mas ainda não conectou ({tunnel['message'] or tunnel['state']}). Sem internet?", {"tunnel_state": tunnel["state"]}

    def op_tunnel_stop(self, args: dict, by: str) -> tuple[str, str, dict]:
        self.stop_tunnel(f"desligado a pedido ({by})")
        self.record("tunel", "tunel_desligado", "ok", detail=f"pedido por {by}")
        return "ok", "Túnel desligado. Os sites continuam funcionando no celular, mas sem link público.", {}

    def op_tunnel_restart(self, args: dict, by: str) -> tuple[str, str, dict]:
        self.stop_tunnel("reinício pedido")
        return self.op_tunnel_start(args, by)

    # ----------------------------------------------------------------- servidores (4.0)
    def _pump_all(self, skip: str | None = None) -> None:
        """Mantém o resto andando enquanto uma operação de servidor espera (o servidor do CDBS, o túnel e os outros apps)."""
        try:
            self.watch_server()
            self.watch_tunnel()
            self.apps.tick(skip=skip)
            self.write_state()
        except Exception:                                                    # noqa: BLE001
            pass

    def _app_name(self, args: dict) -> str:
        name = args.get("name")
        if not isinstance(name, str) or not apps_mod.NAME_RE.fullmatch(name):
            raise apps_mod.AppError("Nome de servidor inválido.")
        self.app_store.get(name)
        return name

    def _app_op(self, func) -> tuple[str, str, dict]:
        try:
            result = func()
        except apps_mod.AppError as exc:
            return "falhou", str(exc), {}
        return result if len(result) == 3 else (*result, {})

    def op_app_start(self, args: dict, by: str) -> tuple[str, str, dict]:
        return self._app_op(lambda: self.apps.op_start(name := self._app_name(args), lambda: self._pump_all(skip=name)))

    def op_app_restart(self, args: dict, by: str) -> tuple[str, str, dict]:
        return self.op_app_start(args, by)                                   # op_start para o que estiver rodando e liga de novo

    def op_app_stop(self, args: dict, by: str) -> tuple[str, str, dict]:
        return self._app_op(lambda: self.apps.op_stop(self._app_name(args)))

    def op_app_deploy(self, args: dict, by: str) -> tuple[str, str, dict]:
        return self._app_op(lambda: self.apps.op_deploy(name := self._app_name(args), lambda: self._pump_all(skip=name)))

    def op_app_rollback(self, args: dict, by: str) -> tuple[str, str, dict]:
        return self._app_op(lambda: self.apps.op_deploy(name := self._app_name(args), lambda: self._pump_all(skip=name), rollback=True))

    def op_app_remove(self, args: dict, by: str) -> tuple[str, str, dict]:
        return self._app_op(lambda: self.apps.op_remove(self._app_name(args)))

    def op_update_apply(self, args: dict, by: str) -> tuple[str, str, dict]:
        return self._install(by, staged_id=str(args.get("staged_id", "")), allow=bool(args.get("allow_same_or_older")))

    def op_update_rollback(self, args: dict, by: str) -> tuple[str, str, dict]:
        return self._install(by, snapshot_id=str(args.get("snapshot_id", "")), allow=True)

    def _install(self, by: str, *, staged_id: str = "", snapshot_id: str = "", allow: bool = False) -> tuple[str, str, dict]:
        upd = self.updater
        lock = upd.lock()
        if not lock.acquire(timeout=1, owner="atualizacao"):
            return "recusado", "Já existe uma atualização em andamento.", {}
        try:
            # Fase 1: nada da versão ativa muda. Qualquer erro aqui = "falhou, nada foi alterado".
            journal = upd.begin(staged_id=staged_id or None, snapshot_id=snapshot_id or None, allow_same_or_older=allow, by=by)
            try:
                self.record("atualizacao", "atualizacao_iniciada", "info", detail=f"{journal['from_version']} → {journal['to_version']} ({by})", ref=journal["id"])
                upd.verify_source(journal)
                upd.make_snapshot(journal, reason="antes de voltar versão" if snapshot_id else "antes de atualizar")
            except updater_mod.UpdateError as exc:
                upd.finish(journal, "FALHOU", f"{exc} Nada foi alterado.")
                self.record("atualizacao", "atualizacao_falhou", "falha", detail=str(exc), ref=journal["id"])
                return "falhou", f"{exc} Nada foi alterado.", {"journal": journal["id"]}
            # Fase 2: troca. A partir daqui, qualquer problema leva à volta para a cópia conferida.
            why = ""
            try:
                upd._set_step(journal, "PARANDO")
                self.say(f"atualizando {journal['from_version']} → {journal['to_version']}: parando o servidor")
                self.stop_server("atualização em andamento")
                self.state["server"]["state"] = "atualizando"
                upd.install(journal)
                self.say("arquivos novos instalados; iniciando a versão nova")
                self.start_server()
                upd._set_step(journal, "VERIFICANDO")
                healthy = self.wait_healthy(self.health_timeout, expect_version=journal["to_version"])
                selftest = self._selftest() if healthy else (False, "")
                if healthy and selftest[0]:
                    done = upd.finish(journal, "CONCLUIDO", f"versão {journal['to_version']} instalada e respondendo")
                    self.record("atualizacao", "atualizacao_concluida", "ok", detail=f"{journal['from_version']} → {journal['to_version']}", ref=journal["id"])
                    self.say(f"atualização concluída: versão {journal['to_version']} no ar")
                    return "ok", done["message"], {"journal": journal["id"], "version": journal["to_version"]}
                why = "a versão nova não respondeu à verificação de saúde" if not healthy else f"o supervisor novo não passou no autoteste ({selftest[1]})"
                reason = self.server.last_reason() if (self.server and not healthy) else ""
                if reason:
                    why += f" ({reason})"
            except Exception as exc:                                         # noqa: BLE001
                why = f"erro durante a instalação ({type(exc).__name__}: {scrub(str(exc), 120)})"
            self.say(f"a versão nova falhou: {why}. Voltando para {journal['from_version']}")
            self.stop_server("voltando versão")
            self.state["server"]["state"] = "atualizando"
            try:
                upd.restore(journal)
            except Exception as exc:                                         # noqa: BLE001
                msg = (f"{why}; e a volta automática falhou: {exc}. A cópia está guardada em "
                       f"data/updates/versions/{journal.get('snapshot')}. Rode: python3 updater.py voltar (com o CDBS parado).")
                upd.finish(journal, "FALHOU_REVERSAO", msg)
                self.record("atualizacao", "atualizacao_falhou", "falha", detail=msg, ref=journal["id"])
                self.state["server"].update(state="parado_por_falhas", message=msg)
                return "falhou", msg, {"journal": journal["id"]}
            self.start_server()
            back = self.wait_healthy(self.health_timeout, expect_version=journal["from_version"])
            msg = f"{why}; a versão anterior {journal['from_version']} foi recolocada" + ("" if back else ", mas também não respondeu")
            upd.finish(journal, "REVERTIDO" if back else "FALHOU_REVERSAO", msg)
            self.record("atualizacao", "atualizacao_revertida" if back else "atualizacao_falhou", "falha", detail=msg, ref=journal["id"])
            return "falhou", msg, {"journal": journal["id"], "reverted": back}
        finally:
            lock.release()
            if self.server is None and self.state["server"]["state"] not in ("parado_por_falhas",):
                self.start_server()

    def _selftest(self) -> tuple[bool, str]:
        try:
            done = subprocess.run([sys.executable, str(self.root / "supervisor.py"), "--selftest"], cwd=self.root, capture_output=True,
                                  text=True, timeout=30, env=dict(os.environ, CDBS_SELFTEST="1"))
        except (OSError, subprocess.TimeoutExpired) as exc:
            return False, type(exc).__name__
        output = (done.stderr or done.stdout).strip()
        return done.returncode == 0, scrub(output.splitlines()[-1] if output else f"saiu com código {done.returncode}", 160)

    # ----------------------------------------------------------------- ciclo de vida
    def _cleanup_orphans(self) -> None:
        old = read_json(self.system / "supervisor.json")
        for part, needle in (("server", b"cdbs.py"), ("tunnel", b"cloudflared")):
            pid = ((old or {}).get(part) or {}).get("pid") if isinstance(old, dict) else None
            if not isinstance(pid, int) or pid <= 1 or pid == os.getpid():
                continue
            try:
                with open(f"/proc/{pid}/cmdline", "rb") as handle:
                    if needle not in handle.read():
                        continue
                os.kill(pid, signal.SIGTERM)
                for _ in range(50):
                    time.sleep(0.1)
                    os.kill(pid, 0)
                os.kill(pid, signal.SIGKILL)
            except OSError:
                continue
            self.say(f"encerrei um processo que tinha sobrado da execução anterior ({part}, pid {pid})")

    def request_stop(self, *_args: object) -> None:
        self._stop.set()

    def run(self) -> int:
        if not self._lock.acquire(timeout=0.5, owner="supervisor"):
            info = read_json(self.system / "supervisor.json")
            pid = info.get("pid") if isinstance(info, dict) else "?"
            print(f"CDBS.OS não iniciou: o supervisor já está rodando (pid {pid}) em outra sessão do Termux. "
                  "Use essa sessão, ou pare com: python3 supervisor.py stop", file=sys.stderr)
            return EXIT_CANNOT_START
        self._started_at = int(time.time())
        try:
            for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
                try:
                    signal.signal(sig, self.request_stop)
                except (ValueError, OSError):
                    pass
            self._cleanup_orphans()
            for pid in self.apps.cleanup_orphans():
                self.say(f"encerrei um servidor (app) que tinha sobrado da execução anterior (pid {pid})")
            self._beat.start(version=versioninfo.load(self.root)["version"])
            recovered = self.updater.recover_interrupted()
            if recovered:
                self.say(f"atualização interrompida encontrada: {recovered.get('message')}")
                self.record("atualizacao", "atualizacao_revertida" if recovered["step"] == "REVERTIDO" else "atualizacao_falhou",
                            "falha", detail=recovered.get("message", ""), ref=recovered.get("id", ""))
            self.record("servico", "supervisor_iniciado", "ok", detail=f"versão {versioninfo.load(self.root)['version']}")
            self.say("supervisor iniciado (Ctrl+C para parar tudo)")
            self.start_server()
            tunnel_cfg = self.settings.get()["tunnel"]
            if tunnel_cfg["autostart"] and tunnel_cfg["mode"] != "off":
                self.start_tunnel()
            while not self._stop.is_set():
                try:
                    self.process_requests()
                    self.watch_server()
                    self.watch_tunnel()
                    self.apps.tick()
                    self.write_state()
                except Exception as exc:                                     # noqa: BLE001 — o guardião não pode morrer por um erro
                    self.say(f"erro interno ignorado ({type(exc).__name__})")
                self._stop.wait(self.poll)
            self.say("parando: servidores (apps), túnel e servidor")
            self.apps.stop_all("supervisor parado")
            try:
                self.apps.write_state()
            except Exception:                                                # noqa: BLE001
                pass
            self.stop_tunnel("supervisor parado")
            self.stop_server("supervisor parado")
            self.record("servico", "supervisor_parado", "ok", detail="desligamento normal")
            state = read_json(self.system / "supervisor.json")
            if isinstance(state, dict):
                state["stopped_at"] = int(time.time())
                write_json_private(self.system / "supervisor.json", state)
            self._beat.stop(clean=True)
            return 0
        finally:
            self._lock.release()


def doctor_checks(ctx) -> list:
    out = []
    info = control.supervisor_alive(ctx.data)
    state = read_json(ctx.data / "system" / "supervisor.json")
    if info:
        server = info.get("server") or {}
        status = "OK" if server.get("state") == "rodando" else "AVISO"
        out.append(("supervisor", "supervisor", "OK", f"rodando desde {fmt_local(info.get('started_at'))} (pid {info.get('pid')})"))
        out.append(("supervisor", "servidor", status, f"{server.get('state')}" + (f" — {server.get('message')}" if server.get("message") else ""),
                    "python3 supervisor.py restart  (depois de corrigir o motivo)" if status != "OK" else ""))
        tunnel = info.get("tunnel") or {}
        t_status = "OK" if tunnel.get("state") == "conectado" else "INFO" if tunnel.get("state") == "desligado" else "AVISO"
        out.append(("supervisor", "túnel", t_status, f"{tunnel.get('state')}" + (f" — {tunnel.get('url')}" if tunnel.get("url") else "")
                    + (f" — {tunnel.get('message')}" if tunnel.get("message") else ""),
                    "Ligue pelo painel (Sistema → Túnel) ou: python3 supervisor.py tunnel start" if t_status == "AVISO" else ""))
    elif isinstance(state, dict) and state.get("pid") and not state.get("stopped_at"):
        out.append(("supervisor", "supervisor", "AVISO", f"não está rodando, e a última execução NÃO terminou normalmente (último sinal {fmt_local(state.get('heartbeat_at'))})",
                    "Provável: o Android encerrou o Termux. Inicie de novo com: bash start.sh"))
    else:
        out.append(("supervisor", "supervisor", "INFO", "não está rodando" + (f" (parou normalmente em {fmt_local(state.get('stopped_at'))})" if isinstance(state, dict) and state.get("stopped_at") else ""),
                    "Inicie com: bash start.sh"))
    return out


# --------------------------------------------------------------------------- linha de comando

def _client_request(op: str, wait: float, args: dict | None = None) -> int:
    data = ROOT / "data"
    if not control.supervisor_alive(data):
        print("O supervisor não está rodando. Inicie com: bash start.sh")
        return 1
    rid = control.submit(data, op, args, by="linha de comando")
    print(f"Pedido enviado: {control.OPERATIONS[op]}. Aguardando o resultado…")
    found = control.wait(data, rid, wait)
    if not found:
        print("O supervisor ainda não respondeu. Veja depois com: python3 supervisor.py status")
        return 1
    print(f"{found['status'].upper()}: {found['message']}")
    return 0 if found["status"] == "ok" else 1


def _status() -> int:
    data = ROOT / "data"
    alive = control.supervisor_alive(data)
    info = read_json(data / "system" / "supervisor.json")
    if not alive:
        print("Supervisor: PARADO" + (f" (último sinal {fmt_local(info.get('heartbeat_at'))})" if isinstance(info, dict) and info.get("heartbeat_at") else ""))
        return 1
    server, tunnel = info.get("server", {}), info.get("tunnel", {})
    print(f"Supervisor: rodando (pid {info['pid']}, desde {fmt_local(info.get('started_at'))}, versão {info.get('version')})")
    print(f"Servidor:   {server.get('state')}" + (f" — {server.get('message')}" if server.get("message") else ""))
    print(f"Túnel:      {tunnel.get('state')}" + (f" — {tunnel.get('url')}" if tunnel.get("url") else "") + (f" — {tunnel.get('message')}" if tunnel.get("message") else ""))
    return 0


def _stop() -> int:
    info = control.supervisor_alive(ROOT / "data")
    if not info:
        print("O supervisor não está rodando.")
        return 0
    os.kill(int(info["pid"]), signal.SIGTERM)
    for _ in range(300):
        time.sleep(0.1)
        try:
            os.kill(int(info["pid"]), 0)
        except OSError:
            print("Supervisor parado.")
            return 0
    print("O supervisor ainda não parou; espere alguns segundos.")
    return 1


def _selftest() -> int:
    """Só carrega os módulos e confere peças essenciais. Não abre porta, não inicia nada."""
    import cdbs                                                               # noqa: PLC0415, F401
    import notify                                                             # noqa: PLC0415, F401
    import site_monitor                                                       # noqa: PLC0415
    import whatsapp                                                           # noqa: PLC0415, F401
    assert site_monitor.SLOTS == 20
    import apps                                                               # noqa: PLC0415
    assert apps.APP_SLOTS == 5 and set(control.OPERATIONS) >= {"app_start", "app_stop", "app_deploy"}
    assert set(control.OPERATIONS) >= {"server_restart", "tunnel_start", "update_apply"}
    assert callable(getattr(Supervisor, "run"))
    print("autoteste OK")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) > 1 and argv[1] == "--selftest":
        return _selftest()
    command = argv[1] if len(argv) > 1 else "run"
    if command == "run":
        return Supervisor().run()
    if command == "status":
        return _status()
    if command == "stop":
        return _stop()
    if command == "restart":
        return _client_request("server_restart", 90)
    if command == "tunnel" and len(argv) > 2 and argv[2] in ("start", "stop", "restart"):
        return _client_request(f"tunnel_{argv[2]}", 90)
    if command == "tunnel" and len(argv) > 2 and argv[2] == "status":
        return _status()
    print(__doc__.split("Uso:", 1)[1].rstrip())
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
