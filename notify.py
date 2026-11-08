"""CDBS.OS — central de notificações (Parte 2.3).

Todo evento importante vira primeiro um registro LOCAL (`data/notifications/events.json`), com horário, categoria,
posição afetada, estado e situação de entrega. Só depois, se o WhatsApp estiver configurado e ligado, uma linha de
envio em segundo plano tenta entregar — sem nunca travar o painel, o servidor ou o monitor.

Regras contra excesso de mensagens:
  • relatório periódico: no máximo um por horário (a chave do horário evita duplicar depois de um reinício);
  • alerta repetido (mesma chave) dentro do intervalo de espera vira "agrupado" (fica no histórico, não é enviado);
  • "voltou ao normal" só é enviado se o alerta de queda tinha sido enviado;
  • tentativas limitadas (3) com espera crescente, e só para erros temporários (rede, limite, falha da Meta).
Situações de entrega: desativado (WhatsApp desligado), pendente, aceito (a API aceitou — NÃO quer dizer que chegou),
enviado/entregue/lido (confirmação que a Meta manda pelo webhook, se configurado), falhou, bloqueado, agrupado.
"""
from __future__ import annotations

import hashlib
import shutil
import threading
import time
from pathlib import Path
from typing import Callable

from sysutil import fmt_hour, fmt_local, mask_phone, read_json, scrub, write_json_private

RETRY_DELAYS = (30, 120)                 # depois da 1ª e da 2ª falha temporária; a 3ª falha é definitiva
MAX_EVENTS = 500
WINDOW_SECONDS = 24 * 3600 - 30 * 60     # janela de atendimento de 24 h, com margem de 30 min
DISK_LOW_MB, DISK_OK_MB = 200, 300

# (categoria, ação do histórico) -> (tipo, título). Ações do histórico de auditoria que viram alerta.
AUDIT_ALERTS = {
    ("servico", "encerramento_inesperado"): ("alerta", "O CDBS parou sem desligar normalmente e voltou"),
    ("servico", "inicio_falhou"): ("alerta", "O CDBS não conseguiu iniciar"),
    ("servico", "servidor_reiniciado"): ("alerta", "O servidor do CDBS caiu e foi reiniciado pelo supervisor"),
    ("servico", "servidor_travado"): ("alerta", "O servidor do CDBS travou e foi reiniciado pelo supervisor"),
    ("servico", "servidor_parado_por_falhas"): ("alerta", "O servidor do CDBS falhou várias vezes seguidas e foi deixado parado"),
    ("tunel", "tunel_caiu"): ("alerta", "O túnel Cloudflare caiu"),
    ("tunel", "tunel_parado_por_falhas"): ("alerta", "O túnel Cloudflare falhou várias vezes seguidas e foi deixado parado"),
    ("tunel", "tunel_link_novo"): ("alerta", "Novo link público"),
    ("tunel", "tunel_conectado"): ("recuperacao", "O túnel Cloudflare voltou"),
    ("atualizacao", "atualizacao_concluida"): ("alerta", "Atualização instalada"),
    ("atualizacao", "atualizacao_revertida"): ("alerta", "Atualização falhou e a versão anterior foi recolocada"),
    ("atualizacao", "atualizacao_falhou"): ("alerta", "Atualização falhou"),
    ("backup", "backup_falhou"): ("alerta", "Um backup falhou"),
    ("restauracao", "restauracao_revertida"): ("alerta", "Uma restauração falhou e foi desfeita"),
    ("restauracao", "restauracao_falhou"): ("alerta", "Uma restauração falhou"),
    # 4.0 — servidores (apps)
    ("servidores", "app_caiu"): ("alerta", "Um servidor (app) caiu e está sendo reiniciado"),
    ("servidores", "app_parado_por_falhas"): ("alerta", "Um servidor (app) falhou várias vezes seguidas e foi deixado parado"),
    ("servidores", "app_implantacao_revertida"): ("alerta", "A implantação de um servidor falhou e a versão anterior voltou"),
    ("servidores", "app_implantacao_falhou"): ("alerta", "A implantação de um servidor falhou"),
    ("servidores", "app_no_ar"): ("recuperacao", "Um servidor (app) voltou a responder"),
}
# recuperação -> chave do alerta que ela encerra
RECOVERS = {("tunel", "tunel_conectado"): "tunel:tunel_caiu", ("servidores", "app_no_ar"): "servidores:app_caiu"}


def _hash(number: str) -> str:
    return hashlib.sha256(number.encode()).hexdigest()[:16]


class NotificationCenter:
    def __init__(self, folder: Path, settings, *, sender_factory: Callable[[], object], audit=None,
                 clock: Callable[[], float] = time.time, disk_path: Path | None = None, overdue: Callable[[], int] | None = None,
                 sleep_poll: float = 2.0, mailer_factory: Callable[[], object] | None = None):
        self.folder = folder
        self.settings = settings
        self._sender_factory = sender_factory
        self._mailer_factory = mailer_factory
        self._audit = audit
        self._clock = clock
        self._disk_path = disk_path
        self._overdue = overdue
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._poll = sleep_poll
        self._last_housekeeping = 0.0

    # ----- persistência
    def _events(self) -> dict:
        data = read_json(self.folder / "events.json")
        return data if isinstance(data, dict) and isinstance(data.get("events"), list) else {"events": [], "seq": 0}

    def _save_events(self, data: dict) -> None:
        data["events"] = data["events"][-MAX_EVENTS:]
        write_json_private(self.folder / "events.json", data)

    def _state(self) -> dict:
        data = read_json(self.folder / "state.json")
        if not isinstance(data, dict):
            data = {}
        for key in ("cooldown", "alerted", "windows"):
            data.setdefault(key, {})
        return data

    def _save_state(self, data: dict) -> None:
        write_json_private(self.folder / "state.json", data)

    # ----- janela de 24 h (o bot avisa quando o dono manda mensagem)
    def record_inbound(self, number: str) -> None:
        with self._lock:
            state = self._state()
            state["windows"][_hash(number)] = int(self._clock())
            self._save_state(state)

    def window_open(self, number: str) -> bool:
        last = self._state()["windows"].get(_hash(number), 0)
        return self._clock() - last < WINDOW_SECONDS

    # ----- criação de eventos
    def emit(self, kind: str, key: str, title: str, text: str, *, category: str = "sistema", params: list[str] | None = None,
             template: str = "alert", slot: int | None = None, slug: str | None = None, state: str | None = None, force: bool = False,
             only: str | None = None) -> dict:
        now = int(self._clock())
        cfg = self.settings.get()
        notif = cfg["notifications"]
        with self._lock:
            data = self._events()
            st = self._state()
            if kind == "relatorio" and any(e.get("key") == key for e in data["events"]):
                return next(e for e in data["events"] if e.get("key") == key)      # o mesmo horário nunca vira dois relatórios
            data["seq"] = int(data.get("seq", 0)) + 1
            event = {"id": f"{now:x}-{data['seq']:x}", "at": now, "kind": kind, "category": category, "key": str(key)[:160],   # chave interna (montada pelo código): sem máscara, senão "relatorio:<horário>" deixaria de bater
                     "title": scrub(title, 120), "text": scrub_text(text), "params": [scrub(p, 200) for p in (params or [])],
                     "template": template, "slot": slot, "slug": slug, "state": state, "deliveries": [], "status": "registrado", "note": ""}
            send = True
            if kind == "alerta":
                last = st["cooldown"].get(key)
                if not notif.get("alerts_enabled", True):
                    send, event["note"] = False, "alertas desligados nas configurações"
                elif not force and last and now - last < int(notif.get("cooldown_minutes", 60)) * 60:
                    send, event["status"], event["note"] = False, "agrupado", f"repetido: já avisado às {fmt_hour(last)}"
                else:
                    st["cooldown"][key] = now
            elif kind == "recuperacao":
                target = self._recovery_target(key)
                if target and target in st["alerted"]:
                    st["alerted"].pop(target, None)
                    st["cooldown"].pop(target, None)
                else:
                    send, event["note"] = False, "nenhum alerta de queda tinha sido enviado"
            elif kind == "relatorio" and not notif.get("report_enabled", True):
                send, event["note"] = False, "relatório periódico desligado nas configurações"
            if send:
                notes = []
                if only in (None, "whatsapp"):
                    ready, why = self.settings.whatsapp_ready()
                    recipients = notif.get("recipients") or []
                    if not notif.get("enabled"):
                        notes.append("WhatsApp desligado")
                    elif not ready:
                        notes.append(f"WhatsApp sem configuração: {why}")
                    elif not recipients:
                        notes.append("nenhum número de destino configurado")
                    else:
                        event["deliveries"] += [{"to": mask_phone(n), "to_hash": _hash(n), "via": "whatsapp", "status": "pendente", "attempts": 0,
                                                 "channel": "", "message_id": "", "error": "", "updated_at": now, "next_try_at": now} for n in recipients]
                if only in (None, "email"):
                    import emailer                                              # noqa: PLC0415
                    mail = cfg["email"]
                    ready, why = self.settings.email_ready()
                    if not mail.get("enabled"):
                        notes.append("e-mail desligado")
                    elif not ready:
                        notes.append(f"e-mail sem configuração: {why}")
                    else:
                        event["deliveries"] += [{"to": emailer.mask_email(a), "to_hash": _hash("mail:" + a), "via": "email", "status": "pendente",
                                                 "attempts": 0, "channel": "email", "message_id": "", "error": "", "updated_at": now,
                                                 "next_try_at": now} for a in mail["recipients"]]
                if event["deliveries"]:
                    event["status"] = "pendente"
                    if kind == "alerta":
                        st["alerted"][key] = now
                else:
                    event["status"], event["note"] = "desativado", "; ".join(notes) + ": ficou só no histórico local"
            data["events"].append(event)
            self._save_events(data)
            self._save_state(st)
        if event["status"] == "pendente":
            self._wake.set()
        return event

    @staticmethod
    def _recovery_target(key: str) -> str | None:
        parts = key.split(":")
        if len(parts) >= 2 and (parts[0], parts[1]) in RECOVERS:
            return RECOVERS[(parts[0], parts[1])] + (":" + ":".join(parts[2:]) if len(parts) > 2 else ":")
        if parts and parts[0] == "posicao":                                 # posicao:<n>:<slug>:recuperada -> posicao:<n>:<slug>
            return ":".join(parts[:3])
        return None

    # ----- ponte com o histórico de auditoria
    def on_audit(self, entry: dict) -> None:
        mapped = AUDIT_ALERTS.get((entry.get("category"), entry.get("action")))
        if not mapped:
            return
        kind, title = mapped
        target = entry.get("target") or ""
        key = f"{entry['category']}:{entry['action']}:{target}"
        if (entry.get("category"), entry.get("action")) == ("tunel", "tunel_link_novo"):
            key = f"tunel:tunel_link_novo:{entry.get('detail', '')[:80]}"           # cada link novo é um aviso diferente
        elif entry.get("category") == "atualizacao" and entry.get("ref"):
            key += f":{entry['ref']}"                                               # cada atualização (e cada volta) é um aviso diferente
        detail = entry.get("detail") or ""
        text = f"CDBS.OS — {title}" + (f" ({target})" if target else "") + (f"\n{detail}" if detail else "") + f"\n{fmt_local(entry.get('at'))}"
        self.emit(kind, key, title, text, category=entry.get("category", "sistema"),
                  params=[title + (f" ({target})" if target else ""), detail or "—", fmt_local(entry.get("at"))])

    def scan_supervisor_audit(self) -> int:
        """O supervisor é outro processo: o que ele grava no histórico é lido aqui periodicamente."""
        if self._audit is None:
            return 0
        with self._lock:
            st = self._state()
            cursor = int(st.get("audit_cursor") or 0)
            seen = set(st.get("audit_seen") or [])
        entries = [e for e in self._audit.query(since=cursor, limit=200) if e.get("actor") == "supervisor"]
        new = [e for e in reversed(entries) if e.get("id") not in seen]
        for entry in new:
            self.on_audit(entry)
        if entries:
            latest = max(int(e.get("at", 0)) for e in entries)
            with self._lock:
                st = self._state()
                st["audit_cursor"] = latest
                st["audit_seen"] = [e["id"] for e in entries if int(e.get("at", 0)) == latest][-50:]
                self._save_state(st)
        return len(new)

    def first_run_cursor(self) -> None:
        """Na primeira vez, não reenviar o passado inteiro do histórico."""
        with self._lock:
            st = self._state()
            if "audit_cursor" not in st:
                st["audit_cursor"] = int(self._clock())
                self._save_state(st)

    # ----- monitoramento
    def on_round(self, report: dict, changes: list, source: str, *, next_at: int | None = None) -> None:
        import site_monitor                                                     # noqa: PLC0415
        if source == "periodico":
            boundary = report.get("boundary") or report["started_at"]
            text = site_monitor.report_text(report, next_at=next_at)
            if changes:
                text = site_monitor.changes_text(changes, report["started_at"]) + "\n\n" + text
            self.emit("relatorio", f"relatorio:{boundary}", "Relatório de sites", text, category="monitor",
                      params=site_monitor.template_params(report), template="report")
            # A mudança só vai "dentro do relatório" se o relatório periódico estiver ligado. Com ele desligado (ex.: para
            # economizar mensagens), a queda precisa sair como alerta próprio — senão ninguém seria avisado.
            folded = bool(self.settings.get()["notifications"].get("report_enabled", True))
            for old, new in changes:
                self._track_slot_change(old, new, report, send=not folded)
            return
        for old, new in changes:
            self._track_slot_change(old, new, report, send=True)

    def _track_slot_change(self, old: dict, new: dict, report: dict, *, send: bool) -> None:
        import site_monitor                                                     # noqa: PLC0415
        key = f"posicao:{new['slot']}:{new['slug']}"
        bad = new["state"] in ("FORA_DO_AR", "SO_LOCAL")
        was_bad = old.get("state") in ("FORA_DO_AR", "SO_LOCAL")
        when = fmt_local(report["started_at"])
        if bad:
            title = f"Posição {new['slot']} ({new['slug']}): {site_monitor.label(new['state'])}"
            text = site_monitor.changes_text([(old, new)], report["started_at"])
            if send:
                self.emit("alerta", key, title, text, category="monitor", params=[title, new.get("reason") or "—", when],
                          slot=new["slot"], slug=new["slug"], state=new["state"])
            else:                                                               # já foi no relatório: só marca que houve aviso
                with self._lock:
                    st = self._state()
                    st["alerted"][key] = int(self._clock())
                    st["cooldown"][key] = int(self._clock())
                    self._save_state(st)
        elif was_bad and new["state"] == "ATIVO":
            title = f"Posição {new['slot']} ({new['slug']}) voltou ao ar"
            self.emit("recuperacao", f"{key}:recuperada", title, f"CDBS.OS — {title}\n{when}", category="monitor",
                      params=[title, "verificação OK", when], slot=new["slot"], slug=new["slug"], state=new["state"])

    # ----- teste e pré-visualização
    def send_test(self, preview_text: str, preview_params: list[str], only: str | None = "whatsapp") -> dict:
        return self.emit("teste", f"teste:{only or 'todos'}:{int(self._clock())}", "Mensagem de teste do CDBS.OS",
                         "CDBS.OS — mensagem de teste (você pediu pelo painel).\n\n" + preview_text, category="notificacao",
                         params=preview_params, template="report", force=True, only=only)

    # ----- entrega
    def on_status(self, message_id: str, status: str, error: dict | None = None) -> bool:
        """Atualização vinda do webhook da Meta (sent/delivered/read/failed)."""
        names = {"sent": "enviado", "delivered": "entregue", "read": "lido", "failed": "falhou"}
        if status not in names or not message_id:
            return False
        with self._lock:
            data = self._events()
            for event in reversed(data["events"]):
                for delivery in event["deliveries"]:
                    if delivery.get("message_id") == message_id:
                        order = ["aceito", "enviado", "entregue", "lido"]
                        current = delivery.get("status")
                        new = names[status]
                        if new == "falhou" or current not in order or order.index(new) > order.index(current):
                            delivery["status"] = new
                            delivery["updated_at"] = int(self._clock())
                            if error:
                                delivery["error"] = scrub(f"{error.get('code', '')} {error.get('title', '')}", 160)
                            event["status"] = self._overall(event)
                            self._save_events(data)
                        return True
        return False

    @staticmethod
    def _overall(event: dict) -> str:
        statuses = [d["status"] for d in event["deliveries"]]
        if not statuses:
            return event.get("status", "registrado")
        for name in ("pendente", "falhou", "bloqueado"):
            if name in statuses:
                return name
        order = ["aceito", "enviado", "entregue", "lido"]
        if all(s in order for s in statuses):
            return min(statuses, key=order.index)              # a confirmação mais fraca entre os destinatários
        return statuses[0]

    def deliver_pending(self) -> int:
        """Uma passada pela fila. Chamado pela linha de envio (e direto pelos testes)."""
        now = int(self._clock())
        work = []
        with self._lock:
            data = self._events()
            for event in data["events"]:
                for delivery in event["deliveries"]:
                    if delivery["status"] == "pendente" and delivery.get("next_try_at", 0) <= now:
                        work.append((event["id"], delivery["to_hash"]))
        if not work:
            return 0
        full = self.settings.get()
        recipients = {_hash(n): n for n in full["notifications"].get("recipients") or []}
        recipients.update({_hash("mail:" + a): a for a in full["email"].get("recipients") or []})
        cfg = full["whatsapp"]
        done = 0
        for event_id, to_hash in work:
            number = recipients.get(to_hash)
            with self._lock:
                data = self._events()
                event = next((e for e in data["events"] if e["id"] == event_id), None)
                delivery = next((d for d in (event or {}).get("deliveries", []) if d["to_hash"] == to_hash), None)
                if not event or not delivery or delivery["status"] != "pendente":
                    continue
                snapshot = dict(event)
                via = delivery.get("via", "whatsapp")
            outcome = self._send_mail(snapshot, number) if via == "email" else self._send_one(snapshot, number, cfg)
            with self._lock:
                data = self._events()
                event = next((e for e in data["events"] if e["id"] == event_id), None)
                delivery = next((d for d in (event or {}).get("deliveries", []) if d["to_hash"] == to_hash), None)
                if not event or not delivery:
                    continue
                delivery["attempts"] = int(delivery.get("attempts", 0)) + 1
                delivery["updated_at"] = int(self._clock())
                delivery.update({k: v for k, v in outcome.items() if k in ("status", "channel", "message_id", "error")})
                if outcome["status"] == "pendente":
                    attempt = delivery["attempts"]
                    if attempt > len(RETRY_DELAYS):
                        delivery["status"] = "falhou"
                    else:
                        delivery["next_try_at"] = int(self._clock()) + RETRY_DELAYS[attempt - 1]
                event["status"] = self._overall(event)
                self._save_events(data)
                done += 1
            if self._audit is not None and outcome["status"] in ("aceito", "enviado", "falhou", "bloqueado"):
                self._audit.record("notificacao", f"envio_{snapshot['kind']}", "ok" if outcome["status"] in ("aceito", "enviado") else "falha",
                                   actor="notificacoes", target=delivery.get("to", ""), detail=outcome.get("error") or snapshot["title"])
        return done

    def _send_mail(self, event: dict, address: str | None) -> dict:
        from emailer import EmailError                                         # noqa: PLC0415
        if not address:
            return {"status": "falhou", "error": "o e-mail foi removido das configurações antes do envio"}
        try:
            mailer = self._mailer_factory() if self._mailer_factory else None
        except EmailError as exc:
            return {"status": "bloqueado", "channel": "email", "error": str(exc)}
        except Exception:                                                       # noqa: BLE001
            return {"status": "bloqueado", "channel": "email", "error": "e-mail sem configuração válida"}
        if mailer is None:
            return {"status": "bloqueado", "channel": "email", "error": "e-mail sem configuração válida"}
        body = event["text"] + "\n\n—\nAviso automático do CDBS.OS (veja os detalhes no painel). Para parar de receber: painel → Notificações → E-mail."
        try:
            result = mailer.send(address, f"CDBS.OS — {event['title']}", body)
            return {"status": "enviado", "channel": "email", "message_id": result.get("message_id", ""), "error": ""}
        except EmailError as exc:
            return {"status": "pendente" if exc.retryable else "falhou", "channel": "email", "error": f"{exc.code}: {exc}"}
        except Exception as exc:                                                # noqa: BLE001
            return {"status": "pendente", "channel": "email", "error": f"erro inesperado no envio ({type(exc).__name__})"}

    def _send_one(self, event: dict, number: str | None, cfg: dict) -> dict:
        from whatsapp import WhatsAppError                                    # noqa: PLC0415
        if not number:
            return {"status": "falhou", "error": "o número foi removido das configurações antes do envio"}
        try:
            sender = self._sender_factory()
        except WhatsAppError as exc:
            return {"status": "bloqueado", "error": str(exc)}
        except Exception:                                                       # noqa: BLE001
            return {"status": "bloqueado", "error": "WhatsApp sem configuração válida"}
        use_text = self.window_open(number)
        try:
            if use_text:
                result = sender.send_text(number, event["text"])
                channel = "texto"
            else:
                name = cfg["template_report"] if event.get("template") == "report" else cfg["template_alert"]
                if not name:
                    return {"status": "bloqueado", "channel": "modelo",
                            "error": "fora da janela de 24 h e sem modelo (template) aprovado configurado"}
                params = event.get("params") or []
                if event.get("template") == "report":
                    params = (params + ["—"] * 12)[:12]
                    if int(cfg.get("report_variables", 12)) == 3:              # modelo resumido aprovado na Meta
                        import site_monitor                                    # noqa: PLC0415
                        params = site_monitor.compact_params(params)
                else:
                    params = (params + ["—"] * 3)[:3]
                result = sender.send_template(number, name, cfg["template_language"], params)
                channel = "modelo"
            return {"status": "aceito", "channel": channel, "message_id": result.get("message_id", ""), "error": ""}
        except WhatsAppError as exc:
            if exc.code == "JANELA_FECHADA" and use_text:
                with self._lock:                                               # a janela que achávamos aberta fechou: próxima tentativa usa modelo
                    st = self._state()
                    st["windows"].pop(_hash(number), None)
                    self._save_state(st)
                return {"status": "pendente", "channel": "texto", "error": str(exc)}
            return {"status": "pendente" if exc.retryable else "falhou", "channel": "modelo" if not use_text else "texto", "error": f"{exc.code}: {exc}"}
        except Exception as exc:                                                # noqa: BLE001
            return {"status": "pendente", "error": f"erro inesperado no envio ({type(exc).__name__})"}

    # ----- manutenção periódica (espaço em disco, backups atrasados, eventos do supervisor)
    def housekeeping(self) -> None:
        now = self._clock()
        if now - self._last_housekeeping < 60:
            return
        self._last_housekeeping = now
        try:
            self.scan_supervisor_audit()
        except Exception:                                                       # noqa: BLE001
            pass
        if self._disk_path is not None:
            try:
                free_mb = shutil.disk_usage(self._disk_path).free // (1024 * 1024)
            except OSError:
                free_mb = None
            if free_mb is not None:
                st = self._state()
                if free_mb < DISK_LOW_MB:
                    self.emit("alerta", "sistema:espaco_baixo", "Pouco espaço livre no celular",
                              f"CDBS.OS — só {free_mb} MB livres. Backups e publicações podem falhar.", category="sistema",
                              params=["Pouco espaço livre no celular", f"{free_mb} MB livres", fmt_local(now)])
                elif free_mb > DISK_OK_MB and "sistema:espaco_baixo" in st["alerted"]:
                    st["alerted"].pop("sistema:espaco_baixo", None)
                    st["cooldown"].pop("sistema:espaco_baixo", None)
                    self._save_state(st)
        if self._overdue is not None:
            try:
                overdue = int(self._overdue())
            except Exception:                                                   # noqa: BLE001
                overdue = 0
            if overdue:
                self.emit("alerta", "backup:atrasado", "Lojas sem backup há 30 dias",
                          f"CDBS.OS — {overdue} loja(s) sem backup validado há 30 dias ou mais.", category="backup",
                          params=["Lojas sem backup há 30 dias", f"{overdue} loja(s)", fmt_local(now)])

    # ----- linha de envio
    def start(self) -> None:
        if self._thread is not None:
            return
        self.first_run_cursor()
        self._stop.clear()

        def loop() -> None:
            while not self._stop.is_set():
                try:
                    self.deliver_pending()
                    self.housekeeping()
                except Exception:                                               # noqa: BLE001 — a linha de envio nunca morre
                    pass
                self._wake.wait(self._poll)
                self._wake.clear()

        self._thread = threading.Thread(target=loop, name="cdbs-notificacoes", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    # ----- visões
    def list(self, limit: int = 50, kind: str | None = None) -> list[dict]:
        events = [e for e in reversed(self._events()["events"]) if kind is None or e.get("kind") == kind][:max(1, min(limit, 200))]
        out = []
        for event in events:
            view = {k: v for k, v in event.items() if k not in ("params",)}
            view["deliveries"] = [{k: v for k, v in d.items() if k != "to_hash"} for d in event["deliveries"]]
            out.append(view)
        return out

    def last_sent(self) -> dict | None:
        for event in reversed(self._events()["events"]):
            if any(d["status"] in ("aceito", "enviado", "entregue", "lido") for d in event["deliveries"]):
                return {"at": event["at"], "kind": event["kind"], "title": event["title"], "status": event["status"]}
        return None

    def status(self) -> dict:
        cfg = self.settings.get()["notifications"]
        ready, why = self.settings.whatsapp_ready()
        events = self._events()["events"]
        last_failure = next((e for e in reversed(events) if e.get("status") in ("falhou", "bloqueado")), None)
        if not cfg.get("enabled"):
            state, text = "desativado", "WhatsApp desligado (os eventos ficam só no histórico local)"
        elif not ready:
            state, text = "aguardando_configuracao", f"falta configurar: {why}"
        elif not cfg.get("recipients"):
            state, text = "aguardando_configuracao", "falta o número de destino"
        elif last_failure and (not self.last_sent() or last_failure["at"] >= self.last_sent()["at"]):
            state, text = "ultimo_envio_falhou", next((d["error"] for d in last_failure["deliveries"] if d.get("error")), last_failure.get("note", ""))
        else:
            state, text = "pronto", "configurado"
        return {"state": state, "text": scrub(text, 200), "last_sent": self.last_sent(), "email": self.email_status()}

    def email_status(self) -> dict:
        mail = self.settings.get()["email"]
        ready, why = self.settings.email_ready()
        last = None
        for event in reversed(self._events()["events"]):
            mails = [d for d in event["deliveries"] if d.get("via") == "email"]
            if mails:
                last = {"at": event["at"], "title": event["title"], "status": mails[-1]["status"], "error": mails[-1].get("error", "")}
                break
        if not mail["enabled"]:
            state, text = "desativado", "e-mail desligado"
        elif not ready:
            state, text = "aguardando_configuracao", f"falta configurar: {why}"
        elif last and last["status"] in ("falhou", "bloqueado"):
            state, text = "ultimo_envio_falhou", last["error"]
        else:
            state, text = "pronto", "configurado"
        return {"state": state, "text": scrub(text, 200), "last": last}


def scrub_text(text: str) -> str:
    """Texto de mensagem (pode ter várias linhas): limpa linha a linha, sem perder as quebras."""
    return "\n".join(scrub(line, 300) for line in str(text or "").split("\n"))[:3900]


def doctor_checks(ctx) -> list:
    data = read_json(ctx.data / "notifications" / "events.json")
    events = data.get("events", []) if isinstance(data, dict) else []
    if not events:
        return [("notificações", "histórico", "INFO", "nenhuma notificação registrada ainda")]
    failed = [e for e in events[-50:] if e.get("status") in ("falhou", "bloqueado")]
    last = events[-1]
    out = [("notificações", "histórico", "OK", f"{len(events)} registradas; última: {scrub(last.get('title', ''), 80)} ({last.get('status')})")]
    if failed:
        error = next((d.get("error") for d in failed[-1].get("deliveries", []) if d.get("error")), failed[-1].get("note", ""))
        out.append(("notificações", "envios com problema", "AVISO", f"{len(failed)} das últimas 50 falharam ou ficaram bloqueadas; último motivo: {scrub(error, 160)}",
                    "Painel → Notificações: confira token, modelos aprovados e destinatários."))
    return out
