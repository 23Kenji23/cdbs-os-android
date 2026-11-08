"""CDBS.OS — operações do sistema usadas pelo painel E pelo bot (Partes 2.4 e 3).

Um só lugar decide o estado real e as ações permitidas, para o painel e o WhatsApp nunca discordarem.
Toda ação aqui é uma função da lista permitida; nenhuma recebe texto para executar.
"""
from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Callable

import apps as apps_mod
import control
import site_monitor
from sysutil import fmt_local, read_json, scrub

QUICK_URL_RE = re.compile(r"^https://[-a-z0-9]+\.trycloudflare\.com$")
BACKUP_KINDS = ("manual", "seguranca", "importado", "migrado")


APPS_NEED_RESTART = ("O guardião que está rodando é da versão anterior e ainda não sabe cuidar de servidores. "
                     "Reinicie o CDBS para ativar: Ctrl+C na sessão do start.sh e depois bash start.sh.")


class OpsError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


class Ops:
    def __init__(self, *, root: Path, data: Path, sites: Path, site_port: Callable[[], int], admin_port: Callable[[], int],
                 fixed_public_base: Callable[[], str], audit, settings, publication, monitor_factory: Callable[["Ops"], object],
                 notifications, version: Callable[[], str], started_at: Callable[[], int], maintenance: Callable[[str], bool]):
        self.root, self.data, self.sites = root, data, sites
        self._site_port, self._admin_port, self._fixed_base = site_port, admin_port, fixed_public_base
        self.audit, self.settings, self.publication, self.notifications = audit, settings, publication, notifications
        self._version, self._started_at, self._maintenance = version, started_at, maintenance
        self.monitor = monitor_factory(self)

    # ----- túnel e endereço público
    def supervisor(self) -> dict | None:
        return control.supervisor_alive(self.data)

    def tunnel(self) -> dict:
        sup = self.supervisor()
        if sup:
            info = read_json(self.data / "system" / "tunnel.json")
            info = info if isinstance(info, dict) else {}
            url = info.get("url", "")
            return {"managed": True, "state": info.get("state", "desconhecido"), "mode": info.get("mode", "quick"),
                    "url": url if QUICK_URL_RE.fullmatch(url or "") else "", "url_since": info.get("url_since"),
                    "message": info.get("message", ""), "ready_at": info.get("ready_at")}
        return {"managed": False, "state": "desconhecido", "mode": "", "url": "", "url_since": None,
                "message": "o supervisor não está rodando (CDBS iniciado sem o bash start.sh?): o túnel, se existir, foi aberto à mão e o CDBS não sabe o link"}

    def public_base(self) -> str:
        fixed = (self._fixed_base() or "").rstrip("/")
        if fixed:
            return fixed
        tunnel = self.tunnel()
        return tunnel["url"] if tunnel["state"] == "conectado" and tunnel["url"] else ""

    def public_link(self, slug: str) -> str:
        base = self.public_base()
        return f"{base}/{slug}/" if base else ""

    # ----- visão geral
    def last_backup(self) -> dict | None:
        root = self.data / "backups"
        best = None
        if root.is_dir():
            for folder in root.iterdir():
                if not folder.is_dir():
                    continue
                for path in folder.glob("*.json"):
                    if path.name == "state.json":
                        continue
                    rec = read_json(path)
                    if isinstance(rec, dict) and rec.get("state") == "CONCLUIDO" and rec.get("kind") in BACKUP_KINDS:
                        at = int(rec.get("finished_at") or rec.get("started_at") or 0)
                        if not best or at > best["at"]:
                            best = {"at": at, "slug": folder.name, "id": str(rec.get("id", ""))[:12],
                                    "encrypted": (rec.get("protection") or {}).get("state") == "CRIPTOGRAFADO"}
        return best

    def overview(self) -> dict:
        slots = self.slots()
        sup = self.supervisor()
        upd = read_json(self.data / "updates" / "journal.json")
        notif = self.notifications.status()
        events = self.audit.query(limit=8, result="falha") + self.audit.query(limit=8, result="aviso")
        events.sort(key=lambda e: e.get("at", 0), reverse=True)
        return {
            "version": self._version(), "started_at": self._started_at(), "now": int(time.time()),
            "supervisor": {"running": bool(sup), "server": (sup or {}).get("server", {}), "busy": (sup or {}).get("busy", "")},
            "tunnel": self.tunnel(), "public_base": self.public_base(),
            "slots": slots, "last_backup": self.last_backup(), "notifications": notif, "apps": self.apps_summary(),
            "update": {k: upd.get(k) for k in ("step", "from_version", "to_version", "finished_at", "message")} if isinstance(upd, dict) and upd.get("id") else None,
            "recent_problems": events[:6],
        }

    def slots(self) -> dict:
        view = self.monitor.current()
        for item in view["slots"]:
            item["public_url"] = self.public_link(item["slug"]) if item.get("slug") else ""
            item["local_url"] = f"http://127.0.0.1:{self._site_port()}/{item['slug']}/" if item.get("slug") else ""
            item["paused"] = bool(item.get("slug")) and self.publication.is_paused(item["slug"])
        view["public_base"] = self.public_base()
        return view

    # ----- ações sobre posições e sites
    def check(self, slot: int | None, by: str) -> dict:
        only = None
        if slot is not None:
            if not isinstance(slot, int) or not 1 <= slot <= site_monitor.SLOTS:
                raise OpsError(f"Posição inválida: use um número de 1 a {site_monitor.SLOTS}.")
            only = [slot]
        result = self.monitor.request_round("manual" if slot is None else "manual-posicao", only)
        self.audit.record("monitor", "verificar_agora", "ok" if result["started"] else "recusado", actor=by,
                          target=f"posição {slot}" if slot else "todas", detail=result["message"])
        return result

    def check_blocking(self, slot: int | None, by: str) -> dict:
        """Para o bot: espera o resultado real da verificação (no máximo ~1 min)."""
        only = [slot] if slot else None
        try:
            report = self.monitor.run_round("manual-bot", only, wait=True)
        except site_monitor.MonitorError as exc:
            raise OpsError(str(exc), 409) from None
        self.audit.record("monitor", "verificar_agora", "ok", actor=by, target=f"posição {slot}" if slot else "todas", detail=report["summary"]["text"])
        return report

    def slot_info(self, slot: int) -> dict:
        if not isinstance(slot, int) or not 1 <= slot <= site_monitor.SLOTS:
            raise OpsError(f"Posição inválida: use um número de 1 a {site_monitor.SLOTS}.")
        return self.slots()["slots"][slot - 1]

    def resolve(self, text: str) -> dict:
        """Número da posição OU nome do site -> posição. Usado pelo bot."""
        text = (text or "").strip().lower()
        if text.isdigit():
            return self.slot_info(int(text))
        for item in self.slots()["slots"]:
            if item.get("slug") == text:
                return item
        raise OpsError(f"Não encontrei essa posição nem esse site. Responda com o número (1 a {site_monitor.SLOTS}) ou o nome do site.", 404)

    def pause(self, slug: str, by: str, reason: str = "") -> dict:
        if not (self.sites / slug).is_dir():
            raise OpsError("Esse site não está publicado.", 404)
        info = self.publication.pause(slug, by, reason)
        self.audit.record("site", "site_pausado", "ok", actor=by, target=slug, detail=reason or "pausado pelo painel")
        self._recheck(slug)
        return info

    def resume(self, slug: str, by: str) -> bool:
        changed = self.publication.resume(slug)
        self.audit.record("site", "site_retomado", "ok" if changed else "info", actor=by, target=slug,
                          detail="publicação retomada" if changed else "já estava no ar")
        self._recheck(slug)
        return changed

    def _recheck(self, slug: str) -> None:
        slot = self.monitor.map.slot_of(slug)
        if slot:
            self.monitor.request_round("manual-posicao", [slot])

    def assign(self, slot: int, slug: str | None, by: str, expected_path: str | None = None) -> dict:
        try:
            self.monitor.map.assign(slot, slug, expected_path=expected_path)
        except site_monitor.MonitorError as exc:
            raise OpsError(str(exc), exc.status) from None
        self.audit.record("monitor", "posicao_atribuida" if slug else "posicao_liberada", "ok", actor=by, target=f"posição {slot}", detail=slug or "vaga")
        if slug:
            self.monitor.request_round("manual-posicao", [slot])
        return self.slots()

    def on_published(self, slug: str) -> int | None:
        slot = self.monitor.map.on_published(slug)
        if slot:
            self.audit.record("monitor", "posicao_atribuida", "ok", actor="sistema", target=f"posição {slot}", detail=f"{slug} (publicado agora)")
            self.monitor.request_round("manual-posicao", [slot])
        else:
            self.audit.record("monitor", "site_excedente", "aviso", actor="sistema", target=slug,
                              detail=f"as {site_monitor.SLOTS} posições estão ocupadas: este site fica fora do monitoramento")
        return slot

    def on_removed(self, slug: str) -> int | None:
        slot = self.monitor.map.on_removed(slug)
        self.publication.forget(slug)
        if slot:
            self.audit.record("monitor", "posicao_liberada", "ok", actor="sistema", target=f"posição {slot}", detail=f"{slug} removido")
        return slot

    # ----- pedidos ao supervisor
    def request(self, op: str, args: dict | None, by: str) -> str:
        if op not in control.OPERATIONS:
            raise OpsError("Operação não permitida.", 403)
        sup = self.supervisor()
        if not sup:
            raise OpsError("O supervisor não está rodando, então esta ação não está disponível. Inicie o CDBS com: bash start.sh", 409)
        if op in control.APP_OPERATIONS and "apps" not in (sup.get("features") or []):
            raise OpsError(APPS_NEED_RESTART, 409)
        rid = control.submit(self.data, op, args, by=by)
        self.audit.record("sistema", f"pedido_{op}", "info", actor=by, detail=control.OPERATIONS[op], ref=rid)
        return rid

    # ----- servidores (apps), 4.0
    def apps_view(self) -> dict:
        store = apps_mod.AppStore(self.data)
        sup = self.supervisor()
        ready = bool(sup) and "apps" in (sup.get("features") or [])
        runtime = (store.runtime().get("apps") or {}) if ready else {}
        base = self.public_base()
        items = []
        for cfg in store.list():
            rt = runtime.get(cfg["name"]) or {}
            state = rt.get("state") or ("sem_codigo" if not cfg["has_code"] else "parado")
            if not ready and cfg["enabled"]:
                state = "indisponivel"
            items.append({**cfg, **{k: rt.get(k) for k in ("pid", "started_at", "healthy_at", "restarts_recent", "failures", "next_try_at", "last_exit",
                                                            "message", "rss_kb", "cpu_s", "cpu_pct")},
                          "state": state, "label": apps_mod.label(state), "runtime_label": apps_mod.RUNTIMES.get(cfg["runtime"], cfg["runtime"]),
                          "local_url": f"http://127.0.0.1:{self._site_port()}/{cfg['name']}/", "public_url": f"{base}/{cfg['name']}/" if base else ""})
        return {"slots": apps_mod.APP_SLOTS, "apps": items, "free_slots": store.free_slots(), "port_base": store.port_base,
                "supervisor_ready": ready, "supervisor_running": bool(sup), "note": "" if ready else (APPS_NEED_RESTART if sup else
                "O supervisor não está rodando: os servidores só funcionam com o CDBS iniciado por bash start.sh."),
                "runtimes": apps_mod.RUNTIMES}

    def apps_summary(self) -> dict:
        view = self.apps_view()
        counts: dict[str, int] = {}
        for item in view["apps"]:
            counts[item["state"]] = counts.get(item["state"], 0) + 1
        parts = [f"{n} {apps_mod.label(s).split(' ', 1)[-1].lower()}" for s, n in sorted(counts.items())]
        return {"used": len(view["apps"]), "slots": view["slots"], "counts": counts, "ready": view["supervisor_ready"],
                "text": (", ".join(parts) if parts else "nenhum servidor") + f" · {len(view['apps'])} de {view['slots']} vagas",
                "problems": [i["name"] for i in view["apps"] if i["state"] in ("parado_por_falhas", "reiniciando", "indisponivel")]}

    def request_result(self, rid: str) -> dict | None:
        found = control.result(self.data, rid)
        if found is None:
            return None
        return {k: found.get(k) for k in ("id", "op", "status", "message", "at", "url", "tunnel_state", "version", "reverted", "journal", "code")
                if k in found}

    def wait_request(self, rid: str, timeout: float) -> dict | None:
        found = control.wait(self.data, rid, timeout)
        return self.request_result(rid) if found else None

    # ----- relatórios
    def report_text(self) -> str:
        report = self.monitor.last_report()
        if not report:
            return "CDBS.OS — ainda não há verificação concluída. Toque em Verificar agora."
        return site_monitor.report_text(report, next_at=self.monitor.next_at)

    def preview(self) -> dict:
        report = self.monitor.last_report()
        if not report:
            return {"text": self.report_text(), "params": []}
        return {"text": site_monitor.report_text(report, next_at=self.monitor.next_at), "params": site_monitor.template_params(report)}

    def status_text(self) -> str:
        view = self.slots()
        tunnel = self.tunnel()
        sup = self.supervisor()
        lines = [f"CDBS.OS {self._version()} — status ({fmt_local(time.time())})",
                 f"Servidor: no ar desde {fmt_local(self._started_at())}" + ("" if sup else " (sem supervisor)"),
                 f"Túnel: {tunnel['state']}" + (f" — {tunnel['url']}" if tunnel["url"] else ""),
                 f"Posições: {view['summary']['text']}"]
        last = view.get("last_round")
        if last:
            lines.append(f"Última verificação: {fmt_local(last['started_at'])}")
        problems = [s for s in view["slots"] if s["state"] in ("FORA_DO_AR", "SO_LOCAL", "INCONCLUSIVO")]
        for item in problems[:5]:
            lines.append(f"• {item['slot']}. {item['slug']}: {site_monitor.label(item['state'])}" + (f" ({scrub(item.get('reason', ''), 70)})" if item.get("reason") else ""))
        return "\n".join(lines)
