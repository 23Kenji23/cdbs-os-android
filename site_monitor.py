"""CDBS.OS — monitoramento real das 20 posições de site (Parte 2.1 e 2.2; 20 posições desde a 4.0).

* Exatamente SLOTS (20) posições numeradas, estáveis (`data/monitoring/sites.json`). Cada uma tem um site ou está VAGA.
  Um site nunca ocupa duas posições; remover um site libera a posição dele sem renumerar as outras; sites além de 10
  ficam fora do monitoramento e aparecem como "excedentes" (aviso no painel).
* Verificação de verdade: pedido HTTP local ao site (127.0.0.1:porta dos sites) e, quando há um endereço público
  conhecido (CDBS_PUBLIC_BASE ou o link atual do túnel) e o teste público está ligado, pedido pelo link público.
  "Existe uma pasta" ou "existe um processo" NÃO é prova de que o site está no ar.
* Estados: ATIVO, SO_LOCAL (funciona no aparelho, mas o link público falhou — problema do túnel, não do site),
  FORA_DO_AR, INCONCLUSIVO (sem como concluir: timeout, sem internet), PAUSADO (parado de propósito) e VAGO.
* Rodadas nunca se sobrepõem; uma posição com problema não impede as outras; cada verificação tem prazo curto.
* Agendamento alinhado ao relógio (padrão a cada 30 min: :00 e :30). Ao iniciar faz uma verificação local sem
  enviar relatório; cada horário de relatório é enviado no máximo UMA vez, mesmo com reinícios.
"""
from __future__ import annotations

import http.client
import json
import re
import socket
import ssl
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

from sysutil import ensure_private_dir, fmt_hour, fmt_local, read_json, scrub, write_json_private

MAP_VERSION = 2                 # 1 = 3.0 (10 posições); 2 = 4.0 (20 posições)
SLOTS = 20                      # 10 até a 3.0; o mapa antigo é ampliado sem renumerar nada
SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,30}[a-z0-9])?$")
PATH_RE = re.compile(r"^/[A-Za-z0-9._~/-]{0,200}$")
STATES = {
    "ATIVO": ("🟢", "Ativo"),
    "SO_LOCAL": ("🟠", "Só no aparelho (link público falhou)"),
    "FORA_DO_AR": ("🔴", "Fora do ar"),
    "INCONCLUSIVO": ("🟡", "Verificação inconclusiva"),
    "PAUSADO": ("⏸", "Pausado"),
    "VAGO": ("⚪", "Vago"),
}
SUMMARY_ORDER = (("ATIVO", "ativo", "ativos"), ("SO_LOCAL", "só no aparelho", "só no aparelho"),
                 ("FORA_DO_AR", "fora do ar", "fora do ar"), ("INCONCLUSIVO", "inconclusivo", "inconclusivos"),
                 ("PAUSADO", "pausado", "pausados"), ("VAGO", "vago", "vagos"))
HISTORY_KEEP = 300


class MonitorError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def label(state: str) -> str:
    icon, text = STATES.get(state, ("❔", state))
    return f"{icon} {text}"


def summarize(slots: list[dict]) -> dict:
    counts = {state: 0 for state in STATES}
    for item in slots:
        counts[item["state"]] = counts.get(item["state"], 0) + 1
    parts = [f"{counts[s]} {one if counts[s] == 1 else many}" for s, one, many in SUMMARY_ORDER if counts.get(s)]
    return {"counts": counts, "text": " · ".join(parts) or "nenhuma posição"}


# --------------------------------------------------------------------------- mapa das posições

class SlotMap:
    def __init__(self, path: Path, sites_dir: Path, *, clock: Callable[[], float] = time.time):
        self.path, self.sites, self._clock = path, sites_dir, clock
        self._lock = threading.RLock()
        self.warnings: list[str] = []

    def published(self) -> list[str]:
        if not self.sites.is_dir():
            return []
        return sorted(p.name for p in self.sites.iterdir() if p.is_dir() and SLUG_RE.fullmatch(p.name))

    @staticmethod
    def _empty() -> list[dict]:
        return [{"slot": n, "slug": None, "enabled": True, "expected_path": "/"} for n in range(1, SLOTS + 1)]

    def _initial(self) -> list[dict]:
        slots = self._empty()
        for index, slug in enumerate(self.published()[:SLOTS]):        # só como valor inicial; você reorganiza depois
            slots[index]["slug"] = slug
        return slots

    def load(self) -> list[dict]:
        with self._lock:
            raw = read_json(self.path, default=None) if self.path.exists() else None
            if raw is None or raw == {}:
                if self.path.exists():
                    self._quarantine("ilegível")
                slots = self._initial()
                self._save(slots)
                return slots
            slots, problems = self._normalize(raw)
            if isinstance(raw, dict) and raw.get("version", 1) < MAP_VERSION:
                problems += self._migrate(slots)
            if problems:
                self.warnings = problems
                self._save(slots)
            return slots

    def _migrate(self, slots: list[dict]) -> list[str]:
        """Mapa de uma versão anterior (3.0: 10 posições). As posições que existiam ficam com o mesmo número; sites que
        tinham ficado de fora por falta de vaga entram, em ordem alfabética, nas posições novas. Grava a versão nova."""
        assigned = {s["slug"] for s in slots if s["slug"]}
        moved = []
        for slug in self.published():
            if slug in assigned:
                continue
            free = next((s for s in slots if s["slug"] is None), None)
            if free is None:
                break
            free["slug"] = slug
            moved.append(f"{slug} → posição {free['slot']}")
        note = f"mapa ampliado para {SLOTS} posições (as que já existiam não mudaram de número)"
        return [note + (f"; sites que estavam de fora entraram: {', '.join(moved)}" if moved else "")]

    def _quarantine(self, why: str) -> None:
        target = self.path.with_name(f"sites.json.corrompido-{int(self._clock())}")
        try:
            self.path.replace(target)
        except OSError:
            pass
        self.warnings = [f"o mapa das posições estava {why}; foi guardado como {target.name} e refeito a partir dos sites publicados"]

    def _normalize(self, raw: object) -> tuple[list[dict], list[str]]:
        problems: list[str] = []
        items = raw.get("slots") if isinstance(raw, dict) else None
        if not isinstance(items, list):
            self._quarantine("em formato desconhecido")
            return self._initial(), self.warnings
        slots = self._empty()
        seen: set[str] = set()
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get("slot"), int) or not 1 <= item["slot"] <= SLOTS:
                problems.append("entrada inválida descartada do mapa das posições")
                continue
            target = slots[item["slot"] - 1]
            slug = item.get("slug")
            if slug is not None and (not isinstance(slug, str) or not SLUG_RE.fullmatch(slug)):
                problems.append(f"posição {item['slot']}: nome de site inválido descartado")
                slug = None
            if slug in seen:
                problems.append(f"posição {item['slot']}: {slug} já estava em outra posição; esta ficou vaga")
                slug = None
            if slug:
                seen.add(slug)
            target["slug"] = slug
            target["enabled"] = item.get("enabled", True) is not False
            path = item.get("expected_path", "/")
            target["expected_path"] = path if isinstance(path, str) and PATH_RE.fullmatch(path) and ".." not in path else "/"
        return slots, problems

    def _save(self, slots: list[dict]) -> None:
        write_json_private(self.path, {"version": MAP_VERSION, "slots": slots, "updated_at": int(self._clock())})

    def slot_of(self, slug: str) -> int | None:
        return next((s["slot"] for s in self.load() if s["slug"] == slug), None)

    def extras(self) -> list[str]:
        assigned = {s["slug"] for s in self.load() if s["slug"]}
        return [slug for slug in self.published() if slug not in assigned]

    def assign(self, slot: object, slug: object, *, expected_path: object = None) -> list[dict]:
        if not isinstance(slot, int) or not 1 <= slot <= SLOTS:
            raise MonitorError(f"Posição inválida: use um número de 1 a {SLOTS}.")
        with self._lock:
            slots = self.load()
            if slug is None:
                slots[slot - 1]["slug"] = None
                slots[slot - 1]["expected_path"] = "/"
            else:
                if not isinstance(slug, str) or not SLUG_RE.fullmatch(slug):
                    raise MonitorError("Nome de site inválido.")
                if slug not in self.published():
                    raise MonitorError(f"O site {slug} não está publicado neste celular.", 404)
                other = next((s["slot"] for s in slots if s["slug"] == slug and s["slot"] != slot), None)
                if other:
                    raise MonitorError(f"O site {slug} já está na posição {other}. Libere aquela posição primeiro.", 409)
                if slots[slot - 1]["slug"] not in (None, slug):
                    raise MonitorError(f"A posição {slot} está ocupada por {slots[slot - 1]['slug']}. Libere-a primeiro.", 409)
                slots[slot - 1]["slug"] = slug
            if expected_path is not None:
                if not isinstance(expected_path, str) or not PATH_RE.fullmatch(expected_path) or ".." in expected_path:
                    raise MonitorError("Caminho de verificação inválido (ex.: / ou /index.html).")
                slots[slot - 1]["expected_path"] = expected_path
            self._save(slots)
            return slots

    def on_published(self, slug: str) -> int | None:
        """Site novo vai para a primeira posição vaga. Sem vaga: fica de fora (excedente) e o painel avisa."""
        with self._lock:
            slots = self.load()
            current = next((s["slot"] for s in slots if s["slug"] == slug), None)
            if current:
                return current
            free = next((s for s in slots if s["slug"] is None), None)
            if not free:
                return None
            free["slug"] = slug
            self._save(slots)
            return free["slot"]

    def on_removed(self, slug: str) -> int | None:
        """Remoção CONFIRMADA (pelo painel): a posição volta a ser vaga; as outras não mudam de número."""
        with self._lock:
            slots = self.load()
            for item in slots:
                if item["slug"] == slug:
                    item["slug"] = None
                    item["expected_path"] = "/"
                    self._save(slots)
                    return item["slot"]
            return None


# --------------------------------------------------------------------------- verificações HTTP

def check_local(port: int, path: str, timeout: float) -> dict:
    started = time.monotonic()
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        connection.request("GET", path, headers={"Host": f"127.0.0.1:{port}", "User-Agent": "CDBS-monitor"})
        response = connection.getresponse()
        body = response.read(65536)
        return _classify_http(response.status, response.getheader("Content-Type", ""), body, started, public=False)
    except ConnectionRefusedError:
        return {"result": "falha", "code": "recusada", "reason": "o servidor dos sites não respondeu (conexão recusada)", "ms": _ms(started)}
    except (socket.timeout, TimeoutError):
        return {"result": "inconclusivo", "code": "timeout", "reason": f"sem resposta em {timeout:g} s", "ms": _ms(started)}
    except (OSError, http.client.HTTPException) as exc:
        return {"result": "inconclusivo", "code": "erro", "reason": f"erro de conexão ({type(exc).__name__})", "ms": _ms(started)}
    finally:
        connection.close()


def check_public(url: str, timeout: float) -> dict:
    started = time.monotonic()
    request = urllib.request.Request(url, headers={"User-Agent": "CDBS-monitor", "Cache-Control": "no-cache"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))       # só a URL conhecida, sem proxy do ambiente
    try:
        with opener.open(request, timeout=timeout) as response:                  # noqa: S310 — URL montada pelo próprio CDBS
            body = response.read(65536)
            return _classify_http(response.status, response.headers.get("Content-Type", ""), body, started, public=True)
    except urllib.error.HTTPError as error:
        return _classify_http(error.code, error.headers.get("Content-Type", "") if error.headers else "", b"", started, public=True)
    except urllib.error.URLError as error:
        reason = error.reason
        if isinstance(reason, socket.gaierror):
            return {"result": "inconclusivo", "code": "dns", "reason": "não foi possível resolver o endereço (sem internet ou DNS)", "ms": _ms(started)}
        if isinstance(reason, (socket.timeout, TimeoutError)):
            return {"result": "inconclusivo", "code": "timeout", "reason": f"o link público não respondeu em {timeout:g} s", "ms": _ms(started)}
        if isinstance(reason, ssl.SSLError):
            return {"result": "falha", "code": "tls", "reason": "erro de certificado HTTPS no link público", "ms": _ms(started)}
        if isinstance(reason, ConnectionRefusedError):
            return {"result": "falha", "code": "recusada", "reason": "o link público recusou a conexão", "ms": _ms(started)}
        return {"result": "inconclusivo", "code": "rede", "reason": "falha de rede ao abrir o link público", "ms": _ms(started)}
    except (socket.timeout, TimeoutError):
        return {"result": "inconclusivo", "code": "timeout", "reason": f"o link público não respondeu em {timeout:g} s", "ms": _ms(started)}
    except (OSError, ValueError, http.client.HTTPException) as exc:
        return {"result": "inconclusivo", "code": "erro", "reason": f"erro ao abrir o link público ({type(exc).__name__})", "ms": _ms(started)}


def _ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _classify_http(status: int, content_type: str, body: bytes, started: float, *, public: bool) -> dict:
    where = "pelo link público" if public else "no aparelho"
    if status == 200:
        if not body:
            return {"result": "falha", "code": "vazio", "reason": f"a página veio vazia {where}", "ms": _ms(started), "http": status}
        if "html" in content_type.lower() and b"<" not in body:
            return {"result": "falha", "code": "conteudo", "reason": f"a página não parece HTML {where}", "ms": _ms(started), "http": status}
        return {"result": "ok", "code": "200", "reason": "", "ms": _ms(started), "http": status}
    if status == 503:
        return {"result": "inconclusivo", "code": "503", "reason": f"temporariamente indisponível {where} (HTTP 503: manutenção ou sobrecarga)", "ms": _ms(started), "http": status}
    if 300 <= status < 400:
        return {"result": "inconclusivo", "code": str(status), "reason": f"redirecionou (HTTP {status}) {where}", "ms": _ms(started), "http": status}
    hint = {404: "página não encontrada", 530: "o túnel não está ligado ao celular", 502: "o túnel não alcançou o celular",
            1033: "o túnel não está conectado"}.get(status, "erro")
    return {"result": "falha", "code": str(status), "reason": f"HTTP {status} {where} ({hint})", "ms": _ms(started), "http": status}


# --------------------------------------------------------------------------- monitor

class Monitor:
    def __init__(self, data: Path, sites: Path, *, site_port: Callable[[], int], publication, maintenance: Callable[[str], bool],
                 settings, public_base: Callable[[], str], tunnel_state: Callable[[], dict] | None = None,
                 on_round: Callable[[dict, list, str], None] | None = None, audit=None, clock: Callable[[], float] = time.time,
                 local_timeout: float = 5.0, public_timeout: float = 10.0, retries: int = 2, retry_delay: float = 0.5,
                 local_check: Callable = check_local, public_check: Callable = check_public, workers: int = 6):
        self.folder = data / "monitoring"
        self.map = SlotMap(self.folder / "sites.json", sites, clock=clock)
        self.sites = sites
        self._site_port, self._publication, self._maintenance = site_port, publication, maintenance
        self._settings, self._public_base, self._tunnel_state = settings, public_base, tunnel_state or (lambda: {})
        self._on_round, self._audit, self._clock = on_round, audit, clock
        self.local_timeout, self.public_timeout = local_timeout, public_timeout
        self.retries, self.retry_delay = max(1, retries), retry_delay
        self._local_check, self._public_check = local_check, public_check
        self._round_lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="cdbs-monitor")
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.next_at: int | None = None
        self.running_round: dict | None = None
        self._pending: set[int] | None = None          # pedidos que chegaram com uma rodada em andamento (None = nenhum, vazio = todas)
        self._pending_lock = threading.Lock()

    # ----- persistência
    def last_report(self) -> dict | None:
        data = read_json(self.folder / "last_report.json")
        return data if isinstance(data, dict) and isinstance(data.get("slots"), list) else None

    def _state(self) -> dict:
        data = read_json(self.folder / "state.json")
        return data if isinstance(data, dict) else {}

    def _save_state(self, **fields: object) -> None:
        state = self._state()
        state.update(fields)
        write_json_private(self.folder / "state.json", state)

    def _append_history(self, report: dict) -> None:
        ensure_private_dir(self.folder)
        path = self.folder / "history.jsonl"
        compact = {"id": report["id"], "source": report["source"], "started_at": report["started_at"], "duration_ms": report["duration_ms"],
                   "summary": report["summary"]["text"], "states": [s["state"] for s in report["slots"]]}
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(compact, ensure_ascii=False) + "\n")
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
            if len(lines) > HISTORY_KEEP + 100:
                tmp = path.with_suffix(".tmp")
                tmp.write_text("\n".join(lines[-HISTORY_KEEP:]) + "\n", encoding="utf-8")
                tmp.replace(path)
        except OSError:
            pass

    def history(self, limit: int = 50) -> list[dict]:
        path = self.folder / "history.jsonl"
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        out = []
        for raw in reversed(lines[-max(1, min(limit, HISTORY_KEEP)):]):
            try:
                out.append(json.loads(raw))
            except ValueError:
                continue
        return out

    # ----- uma posição
    def _public_url(self, slug: str, path: str) -> str:
        base = (self._public_base() or "").rstrip("/")
        return f"{base}/{slug}{path}" if base else ""

    def _attempts(self, func: Callable[[], dict]) -> dict:
        last: dict = {}
        for attempt in range(self.retries):
            last = func()
            last["attempts"] = attempt + 1
            if last["result"] == "ok":
                return last
            if attempt + 1 < self.retries:
                time.sleep(self.retry_delay)
        return last

    def check_slot(self, item: dict, previous: dict | None, public_enabled: bool) -> dict:
        slug = item.get("slug")
        now = int(self._clock())
        base = {"slot": item["slot"], "slug": slug, "checked_at": now, "expected_path": item.get("expected_path", "/"),
                "last_success_at": (previous or {}).get("last_success_at"), "local": None, "public": None, "reason": ""}
        if not slug:
            return {**base, "state": "VAGO", "label": label("VAGO"), "last_success_at": None}
        try:
            if self._publication.is_paused(slug):
                return {**base, "state": "PAUSADO", "label": label("PAUSADO"), "reason": "publicação pausada pelo administrador"}
            if self._maintenance(slug):
                return {**base, "state": "INCONCLUSIVO", "label": label("INCONCLUSIVO"), "reason": "troca de arquivos de uma restauração em andamento"}
            folder = self.sites / slug
            if not folder.is_dir():
                return {**base, "state": "FORA_DO_AR", "label": label("FORA_DO_AR"),
                        "reason": "a pasta do site não existe (removido fora do painel?). Libere a posição ou publique de novo."}
            path = item.get("expected_path") or "/"
            local = self._attempts(lambda: self._local_check(self._site_port(), f"/{slug}{path}", self.local_timeout))
            base["local"] = local
            if local["result"] != "ok":
                state = "FORA_DO_AR" if local["result"] == "falha" else "INCONCLUSIVO"
                return {**base, "state": state, "label": label(state), "reason": local["reason"]}
            url = self._public_url(slug, path) if public_enabled else ""
            if not public_enabled:
                base["public"] = {"result": "nao_verificado", "reason": "teste pelo link público desligado"}
            elif not url:
                base["public"] = {"result": "nao_verificado", "reason": "sem link público conhecido (túnel desligado?)"}
            else:
                public = self._attempts(lambda: self._public_check(url, self.public_timeout))
                public["url"] = url
                base["public"] = public
                if public["result"] == "falha":
                    return {**base, "state": "SO_LOCAL", "label": label("SO_LOCAL"), "reason": public["reason"], "last_success_at": now}
                if public["result"] != "ok":
                    return {**base, "state": "INCONCLUSIVO", "label": label("INCONCLUSIVO"),
                            "reason": f"funciona no aparelho; link público não confirmado: {public['reason']}", "last_success_at": now}
            return {**base, "state": "ATIVO", "label": label("ATIVO"), "last_success_at": now}
        except Exception as exc:                                                     # noqa: BLE001 — uma posição nunca derruba a rodada
            return {**base, "state": "INCONCLUSIVO", "label": label("INCONCLUSIVO"), "reason": f"erro interno do monitor ({type(exc).__name__})"}

    # ----- rodada
    def run_round(self, source: str = "manual", only: list[int] | None = None, *, wait: bool = True) -> dict:
        if not self._round_lock.acquire(blocking=wait, timeout=120 if wait else -1):
            raise MonitorError("Já existe uma verificação em andamento. Espere terminar.", 409)
        try:
            started, mono = int(self._clock()), time.monotonic()
            self.running_round = {"source": source, "started_at": started, "only": only}
            slots = self.map.load()
            previous = {p["slot"]: p for p in (self.last_report() or {}).get("slots", []) if isinstance(p, dict)}
            settings = self._settings.get()["monitor"]
            public_enabled = bool(settings.get("public_check"))
            wanted = [s for s in slots if only is None or s["slot"] in only]
            futures = {s["slot"]: self._executor.submit(self.check_slot, s, previous.get(s["slot"]), public_enabled) for s in wanted}
            results: dict[int, dict] = {}
            for slot_number, future in futures.items():
                try:
                    results[slot_number] = future.result(timeout=self.local_timeout * self.retries + self.public_timeout * self.retries + 15)
                except Exception:                                                    # noqa: BLE001
                    item = next(s for s in wanted if s["slot"] == slot_number)
                    results[slot_number] = {"slot": slot_number, "slug": item["slug"], "state": "INCONCLUSIVO", "label": label("INCONCLUSIVO"),
                                            "reason": "a verificação passou do tempo máximo", "checked_at": int(self._clock()),
                                            "last_success_at": (previous.get(slot_number) or {}).get("last_success_at"), "local": None, "public": None}
            merged = []
            for item in slots:
                if item["slot"] in results:
                    merged.append(results[item["slot"]])
                else:                                                                # verificação de UMA posição: as outras ficam como estavam
                    old = previous.get(item["slot"])
                    if old and old.get("slug") == item["slug"]:
                        merged.append(old)
                    else:
                        merged.append(self.check_slot(item, None, public_enabled) if not item["slug"] else
                                      {"slot": item["slot"], "slug": item["slug"], "state": "INCONCLUSIVO", "label": label("INCONCLUSIVO"),
                                       "reason": "ainda não verificado", "checked_at": None, "last_success_at": None, "local": None, "public": None})
            tunnel = {}
            try:
                tunnel = self._tunnel_state() or {}
            except Exception:                                                        # noqa: BLE001
                tunnel = {}
            report = {"id": f"{started:x}", "source": source, "started_at": started, "finished_at": int(self._clock()),
                      "duration_ms": int((time.monotonic() - mono) * 1000), "slots": merged, "summary": summarize(merged),
                      "public_check": public_enabled, "public_base": self._public_base() if public_enabled else "",
                      "tunnel": {"state": tunnel.get("state", "desconhecido"), "url": tunnel.get("url", "")},
                      "extras": self.map.extras(), "warnings": list(self.map.warnings), "only": only}
            changes = [(previous.get(s["slot"]), s) for s in merged
                       if s["slot"] in results and previous.get(s["slot"]) and previous[s["slot"]].get("slug") == s["slug"]
                       and previous[s["slot"]].get("state") != s["state"]]
            write_json_private(self.folder / "last_report.json", report)
            self._append_history(report)
            if self._audit is not None:
                failing = [s for s in merged if s["state"] in ("FORA_DO_AR", "SO_LOCAL")]
                self._audit.record("monitor", "verificacao", "aviso" if failing else "ok", actor=source,
                                   detail=f"{report['summary']['text']} em {report['duration_ms']} ms")
            if self._on_round is not None:
                try:
                    self._on_round(report, changes, source)
                except Exception:                                                    # noqa: BLE001 — notificação nunca derruba o monitor
                    pass
            return report
        finally:
            self.running_round = None
            self._round_lock.release()
            self._run_pending()

    def _run_pending(self) -> None:
        with self._pending_lock:
            pending, self._pending = self._pending, None
        if pending is not None and not self._stop.is_set():
            only = sorted(pending) or None
            threading.Thread(target=self._safe_round, args=("manual-fila", only), name="cdbs-monitor-fila", daemon=True).start()

    def request_round(self, source: str = "manual", only: list[int] | None = None) -> dict:
        """Para o painel e o bot: dispara em segundo plano e responde na hora (sem travar o pedido HTTP).
        Se já houver uma rodada, o pedido entra na fila e roda logo que ela terminar (nunca duas ao mesmo tempo)."""
        if self._round_lock.locked():
            with self._pending_lock:
                if only is None or self._pending == set():
                    self._pending = set()                      # "todas" engloba qualquer pedido de uma posição
                else:
                    self._pending = (self._pending or set()) | set(only)
            if not self._round_lock.locked():                  # a rodada acabou neste instante: não deixar o pedido parado na fila
                self._run_pending()
            return {"started": False, "queued": True, "message": "Já existe uma verificação em andamento; esta entra logo em seguida."}
        threading.Thread(target=self._safe_round, args=(source, only), name="cdbs-monitor-manual", daemon=True).start()
        return {"started": True, "message": "Verificação iniciada."}

    def _safe_round(self, source: str, only: list[int] | None) -> None:
        try:
            self.run_round(source, only, wait=False)
        except MonitorError:
            pass
        except Exception:                                                            # noqa: BLE001
            pass

    # ----- agenda
    def interval_seconds(self) -> int:
        return int(self._settings.get()["monitor"]["interval_minutes"]) * 60

    def _boundary_after(self, now: float) -> int:
        step = self.interval_seconds()
        return (int(now) // step + 1) * step

    def tick(self) -> dict | None:
        """Chamado periodicamente pela linha do agendador (e direto pelos testes, com relógio falso)."""
        settings = self._settings.get()["monitor"]
        now = self._clock()
        if not settings.get("enabled", True):
            self.next_at = None
            return None
        if self.next_at is None:
            self.next_at = self._boundary_after(now)
            return None
        if now < self.next_at:
            return None
        boundary = self.next_at
        step = self.interval_seconds()
        latest = int(now) // step * step                                            # celular dormiu várias rodadas: faz UMA, a mais recente
        boundary = max(boundary, latest)
        self.next_at = self._boundary_after(now)
        if self._state().get("last_boundary") == boundary:
            return None                                                             # este horário já teve relatório (reinício no meio)
        report = self.run_round("periodico")
        report["boundary"] = boundary
        self._save_state(last_boundary=boundary, last_periodic_at=int(self._clock()))
        return report

    def start(self, poll: float = 15.0) -> None:
        if self._thread is not None:
            return
        self._stop.clear()

        def loop() -> None:
            try:
                if self._settings.get()["monitor"].get("enabled", True):
                    self.run_round("inicio")
            except Exception:                                                        # noqa: BLE001
                pass
            while not self._stop.wait(poll):
                try:
                    self.tick()
                except Exception:                                                    # noqa: BLE001 — o agendador nunca morre por um erro
                    pass

        self._thread = threading.Thread(target=loop, name="cdbs-monitor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        self._executor.shutdown(wait=False, cancel_futures=True)

    # ----- visões
    def current(self) -> dict:
        report = self.last_report()
        slots = self.map.load()
        by_slot = {s["slot"]: s for s in (report or {}).get("slots", []) if isinstance(s, dict)}
        view = []
        for item in slots:
            last = by_slot.get(item["slot"])
            if last and last.get("slug") == item["slug"]:
                view.append({**last, "expected_path": item["expected_path"]})
            elif not item["slug"]:
                view.append({"slot": item["slot"], "slug": None, "state": "VAGO", "label": label("VAGO"), "checked_at": None,
                             "last_success_at": None, "reason": "", "local": None, "public": None, "expected_path": "/"})
            else:
                view.append({"slot": item["slot"], "slug": item["slug"], "state": "INCONCLUSIVO", "label": label("INCONCLUSIVO"),
                             "checked_at": None, "last_success_at": None, "reason": "ainda não verificado desde a mudança",
                             "local": None, "public": None, "expected_path": item["expected_path"]})
        settings = self._settings.get()["monitor"]
        return {"slots": view, "summary": summarize(view), "last_round": {k: report[k] for k in ("id", "source", "started_at", "duration_ms")} if report else None,
                "next_at": self.next_at, "interval_minutes": settings["interval_minutes"], "enabled": settings["enabled"],
                "public_check": settings["public_check"], "extras": self.map.extras(), "warnings": list(self.map.warnings),
                "running": bool(self.running_round)}


# --------------------------------------------------------------------------- textos do relatório

def report_text(report: dict, *, next_at: int | None = None, title: str = "CDBS.OS — RELATÓRIO DE SITES") -> str:
    lines = [title, f"Verificado: {fmt_local(report.get('started_at'))}", ""]
    for item in report["slots"]:
        if item["state"] == "VAGO":
            lines.append(f"{item['slot']}. {label('VAGO')}")
        else:
            reason = f" ({scrub(item['reason'], 80)})" if item["state"] not in ("ATIVO",) and item.get("reason") else ""
            lines.append(f"{item['slot']}. {label(item['state'])} — {item['slug']}{reason}")
    lines += ["", f"Resumo: {report['summary']['text']}"]
    tunnel = report.get("tunnel") or {}
    if tunnel.get("state") and tunnel["state"] != "desconhecido":
        lines.append(f"Túnel: {tunnel['state']}")
    lines.append("Verificação pública: " + ("ligada" if report.get("public_check") else "desligada (teste só no aparelho)"))
    if next_at:
        lines.append(f"Próxima verificação: ~{fmt_hour(next_at)}")
    return "\n".join(lines)


PAIR_SEP = "  |  "


def _slot_line(item: dict) -> str:
    return f"{item['slot']:02d} " + (label("VAGO") if item["state"] == "VAGO" else f"{label(item['state'])} — {item['slug']}")


def template_params(report: dict) -> list[str]:
    """12 parâmetros de UMA linha cada (a API não aceita quebra de linha dentro de um parâmetro):
    horário · 10 linhas com DUAS posições cada (01|02, 03|04 … 19|20) · resumo. Desde a 4.0 (20 posições)."""
    params = [fmt_local(report.get("started_at"))]
    slots = list(report["slots"])
    for i in range(0, SLOTS, 2):
        params.append(PAIR_SEP.join(_slot_line(item) for item in slots[i:i + 2]) or "—")
    tunnel = (report.get("tunnel") or {}).get("state")
    params.append(report["summary"]["text"] + (f" · túnel {tunnel}" if tunnel and tunnel != "desconhecido" else ""))
    return params


def compact_params(params: list[str]) -> list[str]:
    """Relatório para o modelo RESUMIDO (3 variáveis): horário, resumo e as posições que pedem atenção.
    Sai dos mesmos 12 parâmetros (também de uma linha cada), então os dois modelos mostram a mesma verificação."""
    params = (list(params) + ["—"] * 12)[:12]
    calm = (label("ATIVO"), label("VAGO"))
    attention = []
    for line in params[1:11]:
        for part in line.split(PAIR_SEP):
            number, _, rest = part.strip().partition(" ")
            if rest and not rest.startswith(calm):
                attention.append(f"{number.lstrip('0') or number}) {rest}")
    return [params[0], params[11], "; ".join(attention) if attention else "nenhuma (todas as posições ocupadas estão ativas)"]


def changes_text(changes: list, when: int) -> str:
    lines = [f"CDBS.OS — mudança detectada ({fmt_hour(when)})"]
    for old, new in changes:
        reason = f" ({scrub(new.get('reason', ''), 80)})" if new.get("reason") else ""
        lines.append(f"{new['slot']}. {new['slug']}: {label(old['state'])} → {label(new['state'])}{reason}")
    return "\n".join(lines)


def doctor_checks(ctx) -> list:
    folder = ctx.data / "monitoring"
    out = []
    raw = read_json(folder / "sites.json", default=None) if (folder / "sites.json").exists() else None
    if raw is None:
        out.append(("monitoramento", "posições", "INFO", f"o mapa das {SLOTS} posições ainda não foi criado (é criado no primeiro início)"))
        return out
    slots = raw.get("slots") if isinstance(raw, dict) else None
    if not isinstance(slots, list) or len([s for s in slots if isinstance(s, dict)]) not in (10, SLOTS):   # 10 = mapa da 3.0, ampliado ao iniciar
        out.append(("monitoramento", "posições", "AVISO", "sites.json fora do formato (o CDBS refaz a partir dos sites ao iniciar, guardando uma cópia)", ""))
    else:
        used = [s.get("slug") for s in slots if isinstance(s, dict) and s.get("slug")]
        dup = sorted({s for s in used if used.count(s) > 1})
        sites = ctx.data / "sites"
        missing = [s for s in used if not (sites / s).is_dir()]
        status = "AVISO" if dup or missing else "OK"
        message = f"{len(used)} ocupadas · {SLOTS - len(used)} vagas" + (f" · duplicados: {', '.join(dup)}" if dup else "") \
            + (f" · sem pasta: {', '.join(missing)}" if missing else "")
        out.append(("monitoramento", "posições", status, message, "Ajuste em Painel → Sites → Posições." if status != "OK" else ""))
    report = read_json(folder / "last_report.json")
    if isinstance(report, dict) and report.get("started_at"):
        age = ctx.now - report["started_at"]
        status = "AVISO" if age > 2 * 3600 else "OK"
        out.append(("monitoramento", "última verificação", status, f"{fmt_local(report['started_at'])}: {report.get('summary', {}).get('text', '')}",
                    "O monitor não roda com o CDBS parado: inicie com bash start.sh." if status != "OK" else ""))
    return out
