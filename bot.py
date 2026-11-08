"""CDBS.OS — bot de comandos pelo WhatsApp (Parte 3): !Link, !Status, !Relatorio, !Ajuda, !Cancelar.

Segurança (3.4):
  • só números AUTORIZADOS (formato internacional) — administradores consultam e agem; "só consulta" só consulta;
    número desconhecido não recebe resposta nenhuma (fica só registrado, mascarado);
  • todo evento chega assinado pela Meta (X-Hub-Signature-256) — sem assinatura válida, é recusado antes de ler;
  • cada mensagem é processada UMA vez (identificador guardado em disco — reenvios da Meta são ignorados);
  • mensagem que chega atrasada (mais de 10 min) não executa nada; limite de comandos por minuto e por hora;
  • menu e confirmação expiram (5 min / 2 min); só valem as opções que foram realmente apresentadas;
  • cada opção é uma função da lista permitida. Texto recebido NUNCA vira comando de sistema;
  • ações que mudam algo (retomar site, reiniciar túnel/servidor) só depois de SIM explícito, e a resposta só diz
    "pronto" depois do resultado REAL (o que o supervisor e a verificação devolverem).
"""
from __future__ import annotations

import json
import queue
import re
import secrets
import threading
import time
import unicodedata
from pathlib import Path
from typing import Callable

import site_monitor
import whatsapp
from sysutil import fmt_hour, fmt_local, mask_phone, read_json, scrub, write_json_private

SESSION_TTL = 300
CONFIRM_TTL = 120
MAX_AGE = 600
PER_MINUTE, PER_HOUR = 12, 60
SEEN_KEEP, SEEN_DAYS = 2000, 7
RESULT_WAIT = 600
OPTIONS = {
    1: ("Listar as posições", "Estado atual de cada posição, inclusive as vagas", False),
    2: ("Links públicos ativos", "Endereços que estão respondendo agora", False),
    3: ("Verificar um site", "Faz uma verificação nova de uma posição", False),
    4: ("Iniciar/recuperar site", "Retoma um site pausado ou recupera a publicação", True),
    5: ("Estado do túnel", "Cloudflare Tunnel e link público", False),
    6: ("Iniciar/reiniciar túnel", "Liga ou reinicia o túnel (pede confirmação)", True),
    7: ("Posições vagas", "Quais posições de site estão livres", False),
    8: ("Relatório completo", "Verifica tudo agora e manda o relatório", False),
}
HELP = ("CDBS.OS — comandos\n"
        "!Link — menu de gerenciamento dos sites e do túnel\n"
        "!Link 1 … !Link 8 — atalho direto para uma opção do menu\n"
        "!Status — resumo do servidor, do túnel, das posições de site e dos servidores\n"
        "!Relatorio — verifica tudo agora e manda o relatório detalhado\n"
        "!Ajuda — esta lista\n"
        "!Cancelar — cancela o menu ou a confirmação pendente\n\n"
        "Ações que mudam algo sempre pedem SIM antes.")


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", text).strip().casefold()


class Bot:
    def __init__(self, folder: Path, *, settings, ops, notifications, audit, sender_factory: Callable[[], object],
                 clock: Callable[[], float] = time.time, session_ttl: float = SESSION_TTL, confirm_ttl: float = CONFIRM_TTL,
                 max_age: float = MAX_AGE, per_minute: int = PER_MINUTE, per_hour: int = PER_HOUR):
        self.folder = folder
        self.settings, self.ops, self.notifications, self.audit = settings, ops, notifications, audit
        self._sender_factory, self._clock = sender_factory, clock
        self.session_ttl, self.confirm_ttl, self.max_age = session_ttl, confirm_ttl, max_age
        self.per_minute, self.per_hour = per_minute, per_hour
        self.sessions: dict[str, dict] = {}
        self._hits: dict[str, list[float]] = {}
        self._warned_rate: dict[str, float] = {}
        self._lock = threading.RLock()
        self._queue: queue.Queue = queue.Queue(maxsize=500)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.sent: list[tuple[str, str]] = []                 # (tipo, para) — só para conferência/diagnóstico, sem conteúdo

    # ----------------------------------------------------------------- persistência mínima
    def _seen_path(self) -> Path:
        return self.folder / "bot-seen.json"

    def _already_seen(self, message_id: str) -> bool:
        """Marca a mensagem como vista e diz se ela JÁ tinha sido vista (reenvio da Meta, reinício, duplicata)."""
        if not message_id:
            return False
        with self._lock:
            data = read_json(self._seen_path())
            ids = data.get("ids", {}) if isinstance(data, dict) else {}
            if message_id in ids:
                return True
            now = int(self._clock())
            ids[message_id] = now
            ids = {k: v for k, v in ids.items() if now - v < SEEN_DAYS * 86400}
            if len(ids) > SEEN_KEEP:
                ids = dict(sorted(ids.items(), key=lambda kv: kv[1])[-SEEN_KEEP:])
            write_json_private(self._seen_path(), {"ids": ids})
            return False

    def _pending_path(self) -> Path:
        return self.folder / "bot-pending.json"

    def _pending(self) -> list[dict]:
        data = read_json(self._pending_path())
        return data.get("items", []) if isinstance(data, dict) else []

    def _save_pending(self, items: list[dict]) -> None:
        write_json_private(self._pending_path(), {"items": items[-50:]})

    # ----------------------------------------------------------------- autorização e limites
    def role(self, number: str) -> str | None:
        bot = self.settings.get()["bot"]
        if number in bot["admins"]:
            return "admin"
        if number in bot["viewers"]:
            return "viewer"
        return None

    def _rate_ok(self, number: str) -> bool:
        now = self._clock()
        with self._lock:
            hits = [t for t in self._hits.get(number, []) if now - t < 3600]
            recent = [t for t in hits if now - t < 60]
            if len(recent) >= self.per_minute or len(hits) >= self.per_hour:
                self._hits[number] = hits
                return False
            hits.append(now)
            self._hits[number] = hits
            return True

    # ----------------------------------------------------------------- envio (nunca derruba o bot)
    def _sender(self):
        try:
            return self._sender_factory()
        except Exception:                                                     # noqa: BLE001
            return None

    def reply(self, to: str, text: str) -> bool:
        sender = self._sender()
        if sender is None:
            self.audit.record("bot", "resposta_falhou", "falha", actor=mask_phone(to), detail="WhatsApp sem configuração para responder")
            return False
        try:
            sender.send_text(to, text)
            self.sent.append(("texto", mask_phone(to)))
            return True
        except whatsapp.WhatsAppError as exc:
            self.audit.record("bot", "resposta_falhou", "falha", actor=mask_phone(to), detail=f"{exc.code}: {exc}")
            return False

    def _menu(self, to: str, role: str) -> None:
        allowed = [n for n, (_, _, needs_admin) in OPTIONS.items() if role == "admin" or not needs_admin]
        text = "CDBS.OS — MENU DE LINKS\nEscolha uma opção:\n" + "\n".join(f"{n}. {OPTIONS[n][0]}" for n in allowed) + \
               "\n\nResponda com o número da opção. Digite !Cancelar para sair."
        if role != "admin":
            text += "\n(Seu número só pode consultar: as opções que mudam algo não aparecem.)"
        sender = self._sender()
        if sender is not None and self.settings.get()["bot"]["interactive"]:
            try:
                sender.send_list(to, "Escolha o que fazer. Você também pode responder com o número.", "Ver opções",
                                 [(f"menu:{n}", OPTIONS[n][0], OPTIONS[n][1]) for n in allowed], header="CDBS.OS — Links", footer="!Cancelar para sair")
                self.sent.append(("lista", mask_phone(to)))
                return
            except whatsapp.WhatsAppError:
                pass                                                         # o menu numerado em texto sempre funciona
        self.reply(to, text)

    def _ask_confirmation(self, to: str, session: dict, text: str) -> None:
        token = secrets.token_hex(3)
        session.update(stage="confirm", token=token, expires=self._clock() + self.confirm_ttl)
        body = f"{text}\n\nConfirmar? Responda SIM ou CANCELAR (vale por {int(self.confirm_ttl // 60) or 1} min)."
        sender = self._sender()
        if sender is not None and self.settings.get()["bot"]["interactive"]:
            try:
                sender.send_buttons(to, body, [(f"confirm:{token}:sim", "SIM"), (f"confirm:{token}:nao", "CANCELAR")])
                self.sent.append(("botoes", mask_phone(to)))
                return
            except whatsapp.WhatsAppError:
                pass
        self.reply(to, body)

    # ----------------------------------------------------------------- entrada (webhook)
    def handle_payload(self, payload: dict) -> int:
        """Chamado pelo webhook (já com a assinatura conferida). Só ENFILEIRA: responde rápido para a Meta."""
        items = whatsapp.parse_webhook(payload)
        for item in items:
            try:
                self._queue.put_nowait(item)
            except queue.Full:
                self.audit.record("bot", "fila_cheia", "falha", detail="muitas mensagens ao mesmo tempo; esta foi descartada")
        return len(items)

    def process(self, item: whatsapp.Inbound) -> str:
        """Processa uma mensagem/atualização. Devolve um rótulo do que aconteceu (usado nos testes e no histórico)."""
        if item.kind == "status":
            self.notifications.on_status(item.message_id, item.status, item.error)
            return "status"
        number = item.sender
        masked = mask_phone(number)
        configured = str(self.settings.secret("whatsapp_phone_number_id") or "")
        if item.phone_number_id and configured and item.phone_number_id != configured:
            # O mesmo app da Meta pode ter outros números (ex.: o de teste). Mensagem para outro número não é para este CDBS.
            self.audit.record("bot", "comando_recusado", "recusado", actor=masked, detail="mensagem para outro número de envio do app (ignorada)")
            return "outro_numero"
        if self._already_seen(item.message_id):
            return "duplicada"
        if not self.settings.get()["bot"]["enabled"]:
            return "bot_desligado"
        role = self.role(number)
        if role is None:
            self.audit.record("bot", "comando_recusado", "recusado", actor=masked, detail="número não autorizado (sem resposta)")
            return "nao_autorizado"
        if item.timestamp and self._clock() - item.timestamp > self.max_age:
            self.audit.record("bot", "comando_recusado", "recusado", actor=masked, detail="mensagem atrasada (não executada)")
            self.reply(number, "Comando ignorado: chegou com mais de 10 minutos de atraso (o celular do CDBS estava sem internet?). Envie de novo.")
            return "atrasada"
        if not self._rate_ok(number):
            now = self._clock()
            if now - self._warned_rate.get(number, 0) > 60:
                self._warned_rate[number] = now
                self.reply(number, "Muitos comandos seguidos. Espere um minuto e tente de novo.")
            self.audit.record("bot", "comando_recusado", "recusado", actor=masked, detail="limite de comandos por minuto/hora")
            return "limite"
        self.notifications.record_inbound(number)                               # abre a janela de 24 h (respostas sem modelo)
        try:
            return self._dispatch(number, role, item)
        except Exception as exc:                                                 # noqa: BLE001 — nunca derruba o bot
            self.audit.record("bot", "erro_interno", "falha", actor=masked, detail=type(exc).__name__)
            self.reply(number, "Algo deu errado ao processar o comando. Tente de novo; se repetir, veja o painel.")
            return "erro"

    # ----------------------------------------------------------------- comandos
    def _session(self, number: str) -> dict | None:
        session = self.sessions.get(number)
        if session and session["expires"] < self._clock():
            self.sessions.pop(number, None)
            return {"expired": True}
        return session

    def _dispatch(self, number: str, role: str, item: whatsapp.Inbound) -> str:
        masked = mask_phone(number)
        raw = item.reply_id or item.text
        text = normalize(raw)
        session = self._session(number)
        expired = bool(session and session.get("expired"))
        if expired:
            session = None
        command = text.split(" ")[0] if text.startswith("!") else ""
        if command:
            self.audit.record("bot", "comando_bot", "ok", actor=masked, detail=text[:40])
        if command == "!cancelar":
            self.sessions.pop(number, None)
            self.reply(number, "Ok, cancelado. Nada foi feito.")
            return "cancelado"
        if command == "!ajuda":
            self.reply(number, HELP)
            return "ajuda"
        if command == "!status":
            self.reply(number, self.ops.status_text())
            return "status"
        if command == "!relatorio":
            return self._option(number, role, 8, {})
        if command == "!link":
            rest = text[len("!link"):].strip()
            if rest:
                if not rest.isdigit() or int(rest) not in OPTIONS:
                    self.reply(number, "Opção inválida. Use !Link (menu) ou !Link 1 a !Link 8.")
                    return "invalida"
                return self._option(number, role, int(rest), {})
            self.sessions[number] = {"stage": "menu", "expires": self._clock() + self.session_ttl,
                                     "presented": [n for n, (_, _, adm) in OPTIONS.items() if role == "admin" or not adm]}
            self._menu(number, role)
            return "menu"
        if command:
            self.reply(number, "Comando desconhecido. Envie !Ajuda para ver os comandos.")
            return "desconhecido"
        if session is None:
            if expired:
                self.reply(number, "Esse menu (ou confirmação) expirou. Nada foi feito. Envie !Link para começar de novo.")
                return "expirado"
            if text.startswith("confirm:") or text.startswith("menu:") or text.isdigit() or text in ("sim", "s", "cancelar", "nao"):
                self.reply(number, "Não há menu aberto agora. Envie !Link para começar.")
                return "sem_menu"
            self.reply(number, "Envie !Ajuda para ver os comandos.")
            return "dica"
        stage = session["stage"]
        if stage == "menu":
            choice = text[len("menu:"):] if text.startswith("menu:") else text
            if not choice.isdigit() or int(choice) not in session["presented"]:
                self.reply(number, "Opção inválida. Responda com um dos números do menu (ou !Cancelar).")
                return "invalida"
            return self._option(number, role, int(choice), session)
        if stage == "ask_slot":
            try:
                target = self.ops.resolve(text)
            except Exception as exc:                                             # noqa: BLE001
                self.reply(number, str(exc))
                return "invalida"
            return self._after_slot(number, role, session, target)
        if stage == "confirm":
            answer = text
            if text.startswith("confirm:"):
                parts = text.split(":")
                if len(parts) != 3 or parts[1] != session.get("token"):
                    self.reply(number, "Essa confirmação não vale mais. Envie !Link de novo.")
                    self.sessions.pop(number, None)
                    return "expirado"
                answer = parts[2]
            if answer in ("sim", "s"):
                self.sessions.pop(number, None)
                return self._execute(number, role, session["pending"])
            if answer in ("nao", "n", "cancelar"):
                self.sessions.pop(number, None)
                self.reply(number, "Ok, cancelado. Nada foi feito.")
                return "cancelado"
            self.reply(number, "Responda SIM para confirmar ou CANCELAR para desistir.")
            return "invalida"
        self.sessions.pop(number, None)
        return "invalida"

    def _deny(self, number: str, what: str) -> str:
        self.audit.record("bot", "comando_recusado", "recusado", actor=mask_phone(number), detail=f"sem permissão para {what}")
        self.reply(number, "Seu número só pode consultar. Essa ação precisa de um número administrador.")
        self.sessions.pop(number, None)
        return "sem_permissao"

    def _option(self, number: str, role: str, option: int, session: dict) -> str:
        if OPTIONS[option][2] and role != "admin":
            return self._deny(number, OPTIONS[option][0].lower())
        view = self.ops.slots()
        if option == 1:
            self.sessions.pop(number, None)
            lines = [f"CDBS.OS — {site_monitor.SLOTS} posições"] + [f"{s['slot']}. {site_monitor.label(s['state'])}" + (f" — {s['slug']}" if s.get("slug") else "") for s in view["slots"]]
            last = view.get("last_round")
            lines.append(f"\nResumo: {view['summary']['text']}" + (f"\nVerificado: {fmt_local(last['started_at'])}" if last else ""))
            self.reply(number, "\n".join(lines))
            return "opcao1"
        if option == 2:
            self.sessions.pop(number, None)
            active = [s for s in view["slots"] if s["state"] == "ATIVO" and s.get("public_url")]
            if active:
                self.reply(number, "Links públicos respondendo:\n" + "\n".join(f"{s['slot']}. {s['slug']}: {s['public_url']}" for s in active))
            else:
                why = "o túnel está desligado ou sem link" if not view.get("public_base") else "nenhum site ativo com link público agora"
                self.reply(number, f"Nenhum link público ativo ({why}). Use a opção 5 para ver o túnel.")
            return "opcao2"
        if option in (3, 4):
            self.sessions[number] = {"stage": "ask_slot", "option": option, "expires": self._clock() + self.session_ttl}
            verb = "verificar" if option == 3 else "iniciar/recuperar"
            self.reply(number, f"Qual posição deseja {verb}? Responda com o número (1 a {site_monitor.SLOTS}) ou o nome do site.")
            return f"opcao{option}"
        if option == 5:
            self.sessions.pop(number, None)
            tunnel = self.ops.tunnel()
            text = f"Túnel: {tunnel['state']}"
            if tunnel.get("url"):
                text += f"\nLink: {tunnel['url']}"
            if tunnel.get("message"):
                text += f"\n{tunnel['message']}"
            if not tunnel.get("managed"):
                text += "\n(O CDBS não está rodando pelo supervisor, então não sabe o estado do túnel.)"
            self.reply(number, text)
            return "opcao5"
        if option == 6:
            tunnel = self.ops.tunnel()
            if not tunnel.get("managed"):
                self.sessions.pop(number, None)
                self.reply(number, "Não dá para controlar o túnel: o CDBS foi iniciado sem o supervisor (bash start.sh).")
                return "indisponivel"
            op = "tunnel_restart" if tunnel["state"] in ("conectado", "desconectado", "iniciando", "reiniciando") else "tunnel_start"
            session = {"stage": "confirm", "pending": {"kind": "supervisor", "op": op, "label": "reiniciar o túnel" if op == "tunnel_restart" else "ligar o túnel"},
                       "expires": self._clock() + self.confirm_ttl}
            self.sessions[number] = session
            warn = " Os links param por alguns segundos e, no Quick Tunnel, o endereço MUDA." if op == "tunnel_restart" else ""
            self._ask_confirmation(number, session, f"Você pediu para {session['pending']['label']} (Cloudflare).{warn}")
            return "confirmar"
        if option == 7:
            self.sessions.pop(number, None)
            free = [str(s["slot"]) for s in view["slots"] if s["state"] == "VAGO"]
            self.reply(number, f"Posições vagas: {', '.join(free)}." if free else f"Nenhuma posição vaga: as {site_monitor.SLOTS} estão ocupadas.")
            return "opcao7"
        if option == 8:
            self.sessions.pop(number, None)
            try:
                self.ops.check_blocking(None, by=f"bot {mask_phone(number)}")
            except Exception as exc:                                             # noqa: BLE001
                self.reply(number, f"Não consegui verificar agora ({exc}). Mando o último relatório:")
            self.reply(number, self.ops.report_text())
            return "opcao8"
        return "invalida"

    def _after_slot(self, number: str, role: str, session: dict, target: dict) -> str:
        slot, slug = target["slot"], target.get("slug")
        if session["option"] == 3:
            self.sessions.pop(number, None)
            if not slug:
                self.reply(number, f"A posição {slot} está vaga.")
                return "vaga"
            report = self.ops.check_blocking(slot, by=f"bot {mask_phone(number)}")
            item = report["slots"][slot - 1]
            text = f"Posição {slot} — {slug}: {site_monitor.label(item['state'])}"
            if item.get("reason"):
                text += f"\nMotivo: {item['reason']}"
            local = (item.get("local") or {}).get("result")
            public = (item.get("public") or {}).get("result")
            text += f"\nNo aparelho: {local or '—'} · Link público: {public or 'não testado'}\nVerificado: {fmt_hour(item.get('checked_at'))}"
            self.reply(number, text)
            return "verificado"
        # opção 4: iniciar/recuperar
        if not slug:
            self.sessions.pop(number, None)
            self.reply(number, f"A posição {slot} está vaga. Para usar, publique um site pelo painel (não dá para enviar site pelo WhatsApp).")
            return "vaga"
        state = target["state"]
        if state == "PAUSADO":
            pending = {"kind": "resume", "slug": slug, "slot": slot, "label": f"retomar a publicação de {slug} (posição {slot})"}
            session.update(pending=pending)
            self._ask_confirmation(number, session, f"Você pediu para {pending['label']}. O site volta a abrir para os visitantes.")
            return "confirmar"
        if "recusada" in (target.get("reason") or ""):
            if not self.ops.tunnel().get("managed"):
                self.sessions.pop(number, None)
                self.reply(number, "O servidor dos sites não responde e o CDBS está sem supervisor: reinicie pelo Termux (Ctrl+C e bash start.sh).")
                return "indisponivel"
            pending = {"kind": "supervisor", "op": "server_restart", "label": "reiniciar o servidor do CDBS (todos os sites)"}
            session.update(pending=pending)
            self._ask_confirmation(number, session, f"O servidor dos sites não está respondendo. Você pediu para {pending['label']}: "
                                                    "o painel e os sites ficam fora por alguns segundos.")
            return "confirmar"
        self.sessions.pop(number, None)
        if "pasta do site não existe" in (target.get("reason") or "") or (state == "FORA_DO_AR" and "404" in (target.get("reason") or "")):
            self.reply(number, f"{slug} está {site_monitor.label(state)} ({target.get('reason')}). Os arquivos do site estão faltando: "
                               "restaure um backup pelo painel (a restauração pede a senha de recuperação, que não é enviada por WhatsApp).")
            return "restaurar_no_painel"
        report = self.ops.check_blocking(slot, by=f"bot {mask_phone(number)}")
        item = report["slots"][slot - 1]
        if item["state"] == "ATIVO":
            self.reply(number, f"{slug} já está no ar (posição {slot}, verificado agora). Nada a fazer.")
            return "ja_ativo"
        if item["state"] in ("SO_LOCAL", "INCONCLUSIVO"):
            self.reply(number, f"{slug} funciona no aparelho, mas o link público não confirmou ({item.get('reason')}). "
                               "Use a opção 5 (estado do túnel) ou 6 (reiniciar o túnel).")
            return "problema_tunel"
        self.reply(number, f"{slug}: {site_monitor.label(item['state'])} ({item.get('reason') or 'sem detalhe'}). Veja o painel para corrigir.")
        return "sem_acao"

    def _execute(self, number: str, role: str, pending: dict) -> str:
        masked = mask_phone(number)
        if role != "admin":
            return self._deny(number, pending.get("label", "agir"))
        if pending["kind"] == "resume":
            changed = self.ops.resume(pending["slug"], by=f"bot {masked}")
            report = self.ops.check_blocking(pending["slot"], by=f"bot {masked}")
            item = report["slots"][pending["slot"] - 1]
            ok = item["state"] == "ATIVO"
            self.audit.record("bot", "acao_bot", "ok" if ok else "falha", actor=masked, target=pending["slug"],
                              detail=f"retomar: {'retomado' if changed else 'já estava'}; verificação: {item['state']}")
            self.reply(number, f"Resultado: {pending['slug']} {'voltou ao ar' if ok else 'foi retomado, mas a verificação deu ' + site_monitor.label(item['state'])}"
                               + (f" ({item.get('reason')})" if item.get("reason") and not ok else "") + ".")
            return "executado"
        if pending["kind"] == "supervisor":
            try:
                rid = self.ops.request(pending["op"], {}, by=f"bot {masked}")
            except Exception as exc:                                             # noqa: BLE001
                self.audit.record("bot", "acao_bot", "falha", actor=masked, detail=f"{pending['op']}: {exc}")
                self.reply(number, f"Não foi possível pedir: {exc}")
                return "falhou"
            with self._lock:
                items = self._pending()
                items.append({"rid": rid, "to": number, "label": pending["label"], "op": pending["op"], "at": int(self._clock())})
                self._save_pending(items)
            self.audit.record("bot", "acao_bot", "info", actor=masked, detail=f"{pending['op']} pedido ao supervisor", ref=rid)
            self.reply(number, f"Pedido enviado: {pending['label']}. Aguarde o resultado (eu aviso aqui).")
            return "pedido"
        return "invalida"

    def check_pending_results(self) -> int:
        """Resultados de pedidos ao supervisor. Sobrevive a reinício: se o servidor foi reiniciado pelo pedido,
        quem avisa o resultado é o servidor NOVO, quando ele volta."""
        done = 0
        with self._lock:
            items = self._pending()
        keep = []
        for item in items:
            result = self.ops.request_result(item["rid"])
            if result and result.get("status") not in ("em_andamento", "aguardando"):
                ok = result["status"] == "ok"
                self.audit.record("bot", "acao_bot", "ok" if ok else "falha", actor=mask_phone(item["to"]), detail=f"{item['op']}: {result.get('message', '')}", ref=item["rid"])
                self.reply(item["to"], f"Resultado ({item['label']}): {'✓ ' if ok else '✕ '}{result.get('message', '')}")
                done += 1
            elif self._clock() - item.get("at", 0) > RESULT_WAIT:
                self.reply(item["to"], f"Sem resultado de \"{item['label']}\" depois de 10 minutos. Veja o painel (Sistema).")
                done += 1
            else:
                keep.append(item)
        if done:
            with self._lock:
                original = {i["rid"] for i in items}
                newer = [i for i in self._pending() if i["rid"] not in original]      # pedidos feitos enquanto este laço rodava
                self._save_pending(keep + newer)
        return done

    # ----------------------------------------------------------------- linha de processamento
    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()

        def loop() -> None:
            while not self._stop.is_set():
                try:
                    item = self._queue.get(timeout=2)
                except queue.Empty:
                    item = None
                if item is not None:
                    try:
                        self.process(item)
                    except Exception:                                            # noqa: BLE001
                        pass
                try:
                    self.check_pending_results()
                except Exception:                                                # noqa: BLE001
                    pass

        self._thread = threading.Thread(target=loop, name="cdbs-bot", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def status(self) -> dict:
        bot = self.settings.get()["bot"]
        ready, why = self.settings.whatsapp_ready()
        secret = bool(self.settings.secret("whatsapp_app_secret"))
        verify = bool(self.settings.secret("whatsapp_verify_token"))
        problems = []
        if bot["enabled"]:
            if not ready:
                problems.append(why)
            if not secret:
                problems.append("falta a chave do app (App Secret): os eventos são recusados")
            if not verify:
                problems.append("falta o token de verificação do webhook")
            if not bot["admins"] and not bot["viewers"]:
                problems.append("nenhum número autorizado")
        return {"enabled": bot["enabled"], "ready": bot["enabled"] and not problems, "problems": problems}
