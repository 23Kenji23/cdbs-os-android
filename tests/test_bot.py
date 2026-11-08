"""3.0 / Parte 3 — bot !Link pelo WhatsApp: webhook assinado, aperto de mão, autorização por número, menu (lista
interativa e texto), sessões que expiram, confirmação antes de agir, resultado real, reenvio ignorado, mensagem
atrasada, limite de comandos, falha do provedor e nenhum texto virando comando. Sem WhatsApp de verdade."""
import hashlib
import hmac
import io
import json
import os
import sys
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import backup
import cdbs
import control
from test_catalog import request, stack
from whatsapp import Inbound, WhatsAppError

ADMIN = "5585999998888"
VIEWER = "5585977776666"
STRANGER = "5511900001111"
APP_SECRET = "segredo-do-app-de-teste"
VERIFY = "cdbs-token-de-verificacao-teste"


class FakeSender:
    def __init__(self):
        self.messages = []                 # (tipo, para, conteúdo)
        self.fail = None

    def _go(self, kind, to, content):
        if self.fail:
            raise self.fail
        self.messages.append((kind, to, content))
        return {"message_id": f"wamid.{len(self.messages)}"}

    def send_text(self, to, body):
        return self._go("texto", to, body)

    def send_list(self, to, body, button, rows, header="", footer=""):
        return self._go("lista", to, {"body": body, "rows": rows})

    def send_buttons(self, to, body, buttons):
        return self._go("botoes", to, {"body": body, "buttons": buttons})

    def send_template(self, to, name, language, params):
        return self._go("modelo", to, {"name": name, "params": params})

    def texts(self, to=ADMIN):
        return [c if isinstance(c, str) else c["body"] for k, t, c in self.messages if t == to]


def site_zip(title):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("index.html", f"<h1>{title}</h1>")
    return buffer.getvalue()


def sign(body: bytes, secret=APP_SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


class BotTests(unittest.TestCase):
    def setUp(self):
        self.stack = stack()
        self.urls = self.stack.__enter__()
        self.addCleanup(self.stack.__exit__, None, None, None)
        self.patches = [patch.object(cdbs, "SITE_PORT", int(self.urls["public"].rsplit(":", 1)[1])),
                        patch.object(cdbs, "WEBHOOK_GATE", backup.RateGate(10_000, 60))]
        self.sender = FakeSender()
        self.patches.append(patch.object(cdbs, "_whatsapp_sender", lambda: self.sender))
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)
        settings = cdbs.get_settings()
        settings.update("bot", {"enabled": True, "admins": [ADMIN], "viewers": [VIEWER], "interactive": False})
        settings.set_secret("whatsapp_app_secret", APP_SECRET)
        settings.set_secret("whatsapp_verify_token", VERIFY)
        self.bot = cdbs.get_bot()
        self.clock = [time.time()]
        self.bot._clock = lambda: self.clock[0]
        self.seq = 0
        for slug in ("adega", "loja"):
            (cdbs.SITES / slug).mkdir()
            (cdbs.SITES / slug / "index.html").write_text(f"<h1>{slug}</h1>")
        cdbs.get_ops().monitor.run_round("inicio")

    def msg(self, text="", sender=ADMIN, reply_id="", age=0, message_id=None):
        self.seq += 1
        return Inbound(kind="message", sender=sender, message_id=message_id or f"wamid.in{self.seq}", timestamp=int(self.clock[0] - age),
                       msg_type="interactive" if reply_id else "text", text=text, reply_id=reply_id)

    def say(self, text="", **kw):
        return self.bot.process(self.msg(text, **kw))

    def last(self, to=ADMIN):
        texts = self.sender.texts(to)
        return texts[-1] if texts else ""

    # ----------------------------------------------------------------- webhook HTTP
    def test_webhook_handshake_and_signature_are_enforced(self):
        base = self.urls["public"] + "/_whatsapp/webhook"
        ok = request(f"{base}?hub.mode=subscribe&hub.verify_token={VERIFY}&hub.challenge=987654")
        self.assertEqual((ok[0], ok[2]), (200, b"987654"))
        self.assertEqual(request(f"{base}?hub.mode=subscribe&hub.verify_token=errado&hub.challenge=1")[0], 403)
        self.assertEqual(request(base)[0], 403)
        payload = {"object": "whatsapp_business_account", "entry": [{"changes": [{"field": "messages", "value": {"messages": [
            {"from": ADMIN, "id": "wamid.http1", "timestamp": str(int(time.time())), "type": "text", "text": {"body": "!Ajuda"}}]}}]}]}
        body = json.dumps(payload).encode()
        self.assertEqual(request(base, "POST", body=body, headers={"Content-Type": "application/json"})[0], 401, "sem assinatura")
        self.assertEqual(request(base, "POST", body=body, headers={"X-Hub-Signature-256": sign(body, "outro")})[0], 401)
        self.assertEqual(request(base, "POST", body=body, headers={"X-Hub-Signature-256": sign(body + b" ")})[0], 401)
        bad_json = b"{nao e json"
        self.assertEqual(request(base, "POST", body=bad_json, headers={"X-Hub-Signature-256": sign(bad_json)})[0], 400)
        with patch.object(cdbs, "WEBHOOK_MAX", 10):
            self.assertEqual(request(base, "POST", body=body, headers={"X-Hub-Signature-256": sign(body)})[0], 413)
        status, _, out = request(base, "POST", body=body, headers={"X-Hub-Signature-256": sign(body)})
        self.assertEqual((status, out), (200, b"ok"))
        item = self.bot._queue.get_nowait()
        self.assertEqual(self.bot.process(item), "ajuda")
        self.assertIn("!Link", self.last())
        self.assertEqual(request(base, "PUT", body=body, headers={"X-Hub-Signature-256": sign(body)})[0], 405)
        cdbs.get_settings().clear_secret("whatsapp_app_secret")
        self.assertEqual(request(base, "POST", body=body, headers={"X-Hub-Signature-256": sign(body)})[0], 503, "sem chave do app nada é aceito")
        self.assertIn("webhook_recusado", [e["action"] for e in cdbs.get_audit().query(category="bot")])

    def test_delivery_statuses_reach_the_notification_center(self):
        center = cdbs.get_notifications()
        cdbs.get_settings().set_secret("whatsapp_phone_number_id", "123456789")
        cdbs.get_settings().set_secret("whatsapp_token", "EAAGtokenFALSO0123456789abcdefABCDEF00")
        cdbs.get_settings().update("notifications", {"enabled": True, "recipients": [ADMIN]})
        with patch.object(center, "_sender_factory", lambda: self.sender):
            center.emit("alerta", "t:1", "Teste", "x")
            center.deliver_pending()
        message_id = center.list(1)[0]["deliveries"][0]["message_id"]
        self.bot.process(Inbound(kind="status", message_id=message_id, status="delivered", recipient=ADMIN))
        self.assertEqual(center.list(1)[0]["status"], "entregue")

    # ----------------------------------------------------------------- autorização
    def test_only_authorized_numbers_and_viewers_cannot_act(self):
        self.assertEqual(self.say("!Link", sender=STRANGER), "nao_autorizado")
        self.assertEqual(self.sender.texts(STRANGER), [], "número desconhecido não recebe NADA")
        refused = cdbs.get_audit().query(category="bot", result="recusado")
        self.assertTrue(refused)
        self.assertNotIn(STRANGER, json.dumps(refused))
        self.assertEqual(self.say("!Link", sender=VIEWER), "menu")
        menu = self.last(VIEWER)
        self.assertIn("1. Listar as posições", menu)
        self.assertNotIn("Iniciar/reiniciar túnel", menu)
        self.assertEqual(self.say("6", sender=VIEWER), "invalida", "opção que não foi apresentada não vale")
        self.assertEqual(self.say("!Link 6", sender=VIEWER), "sem_permissao")
        self.assertEqual(self.say("!Link 4", sender=VIEWER), "sem_permissao")
        self.assertEqual(self.say("!Status", sender=VIEWER), "status")
        # mensagem que a Meta entregou para OUTRO número de envio do mesmo app: não é para este CDBS
        cdbs.get_settings().set_secret("whatsapp_phone_number_id", "123456789")
        before = len(self.sender.messages)
        other = self.msg("!Status")
        other.phone_number_id = "999999999"
        self.assertEqual(self.bot.process(other), "outro_numero")
        self.assertEqual(len(self.sender.messages), before, "nenhuma resposta sai pelo número configurado")
        mine = self.msg("!Status")
        mine.phone_number_id = "123456789"
        self.assertEqual(self.bot.process(mine), "status")
        cdbs.get_settings().update("bot", {"enabled": False})
        self.assertEqual(self.say("!Status"), "bot_desligado")

    # ----------------------------------------------------------------- menu e opções
    def test_menu_text_and_interactive_list_with_real_states(self):
        self.assertEqual(self.say("!Link"), "menu")
        self.assertIn("CDBS.OS — MENU DE LINKS", self.last())
        self.assertIn("8. Relatório completo", self.last())
        self.assertEqual(self.say("1"), "opcao1")
        listing = self.last()
        self.assertIn("1. 🟢 Ativo — adega", listing)
        self.assertIn("3. ⚪ Vago", listing)
        self.assertEqual(len([l for l in listing.splitlines() if l[:2].rstrip(".").isdigit()]), 20)
        self.assertEqual(self.say("1"), "sem_menu", "depois da resposta o menu fecha")
        cdbs.get_settings().update("bot", {"interactive": True})
        self.assertEqual(self.say("!link"), "menu")
        kind, to, content = self.sender.messages[-1]
        self.assertEqual(kind, "lista")
        self.assertEqual([r[0] for r in content["rows"]], [f"menu:{n}" for n in range(1, 9)])
        self.assertTrue(all(len(r[1]) <= 24 for r in content["rows"]))
        self.assertEqual(self.say(reply_id="menu:7"), "opcao7")
        self.assertIn("Posições vagas: 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20", self.last())
        self.assertEqual(self.say("!Link 2"), "opcao2")
        self.assertIn("Nenhum link público ativo", self.last())
        self.assertEqual(self.say("!Link 5"), "opcao5")
        self.assertIn("supervisor", self.last())

    def test_invalid_input_expiry_cancel_and_no_shell(self):
        self.say("!Link")
        self.assertEqual(self.say("9"), "invalida")
        self.assertEqual(self.say("rm -rf /"), "invalida")
        self.assertEqual(self.say("!link; rm -rf /"), "desconhecido", "o texto nunca vira comando")
        self.assertEqual(self.say("!link 1; reboot"), "invalida")
        self.assertEqual(control.pending(cdbs.DATA), [], "nenhum pedido ao supervisor saiu de texto livre")
        self.assertTrue((cdbs.SITES / "adega" / "index.html").exists())
        self.assertEqual(self.say("!Cancelar"), "cancelado")
        self.assertEqual(self.say("1"), "sem_menu")
        self.say("!Link")
        self.clock[0] += 301
        self.assertEqual(self.say("1"), "expirado")
        self.assertIn("expirou", self.last())
        self.assertEqual(self.say("!Foo"), "desconhecido")
        self.assertEqual(self.say("bom dia"), "dica")
        self.assertEqual(self.say("!Ajuda"), "ajuda")
        self.assertEqual(self.say("!Relatório"), "opcao8", "acentos e maiúsculas são normalizados")
        self.assertIn("RELATÓRIO DE SITES", self.last())

    def test_check_one_site_reports_the_real_result(self):
        self.say("!Link 3")
        self.assertIn("Qual posição", self.last())
        self.assertEqual(self.say("adega"), "verificado")
        self.assertIn("Posição 1 — adega: 🟢 Ativo", self.last())
        (cdbs.SITES / "loja" / "index.html").unlink()
        self.say("!Link 3")
        self.assertEqual(self.say("2"), "verificado")
        self.assertIn("Fora do ar", self.last())
        self.say("!Link 3")
        self.assertEqual(self.say("99"), "invalida")
        self.assertEqual(self.say("5"), "vaga")

    # ----------------------------------------------------------------- ações com confirmação
    def test_resume_paused_site_needs_yes_and_reports_verified_result(self):
        cdbs.get_ops().pause("adega", "teste")
        cdbs.get_ops().monitor.run_round("manual")
        self.say("!Link 4")
        self.assertEqual(self.say("1"), "confirmar")
        self.assertIn("Confirmar? Responda SIM ou CANCELAR", self.last())
        self.assertEqual(self.say("talvez"), "invalida")
        self.assertEqual(self.say("cancelar"), "cancelado")
        self.assertTrue(cdbs.get_publication().is_paused("adega"), "cancelar não faz nada")
        self.say("!Link 4")
        self.say("1")
        self.clock[0] += 121
        self.assertEqual(self.say("sim"), "expirado", "confirmação vencida não executa")
        self.assertTrue(cdbs.get_publication().is_paused("adega"))
        self.say("!Link 4")
        self.say("adega")
        self.assertEqual(self.say("SIM"), "executado")
        self.assertFalse(cdbs.get_publication().is_paused("adega"))
        self.assertIn("Resultado: adega voltou ao ar", self.last())
        self.assertEqual(request(self.urls["public"] + "/adega/")[0], 200)
        self.assertIn(("acao_bot", "ok"), [(e["action"], e["result"]) for e in cdbs.get_audit().query(category="bot")])
        self.say("!Link 4")
        self.assertEqual(self.say("1"), "ja_ativo")
        self.say("!Link 4")
        self.assertEqual(self.say("4"), "vaga")

    def test_interactive_confirmation_token_must_match(self):
        cdbs.get_settings().update("bot", {"interactive": True})
        cdbs.get_ops().pause("adega", "teste")
        cdbs.get_ops().monitor.run_round("manual")
        self.say("!Link 4")
        self.say("1")
        kind, _, content = self.sender.messages[-1]
        self.assertEqual(kind, "botoes")
        good = content["buttons"][0][0]
        self.assertTrue(good.endswith(":sim"))
        self.assertEqual(self.say(reply_id="confirm:000000:sim"), "expirado", "botão de uma confirmação antiga não vale")
        self.assertTrue(cdbs.get_publication().is_paused("adega"))
        self.say("!Link 4")
        self.say("1")
        token_button = self.sender.messages[-1][2]["buttons"][0][0]
        self.assertEqual(self.say(reply_id=token_button), "executado")

    def test_tunnel_restart_goes_through_supervisor_and_result_arrives_later(self):
        self.assertEqual(self.say("!Link 6"), "indisponivel")
        system = cdbs.DATA / "system"
        system.mkdir(parents=True, exist_ok=True)
        (system / "supervisor.json").write_text(json.dumps({"pid": os.getpid(), "heartbeat_at": time.time(), "started_at": 1}))
        (system / "tunnel.json").write_text(json.dumps({"state": "conectado", "url": "https://abc-def.trycloudflare.com", "mode": "quick"}))
        self.assertEqual(self.say("!Link 5"), "opcao5")
        self.assertIn("https://abc-def.trycloudflare.com", self.last())
        self.assertEqual(self.say("!Link 6"), "confirmar")
        self.assertIn("o endereço MUDA", self.last())
        self.assertEqual(self.say("sim"), "pedido")
        pending = control.pending(cdbs.DATA)
        self.assertEqual([p["op"] for p in pending], ["tunnel_restart"], "só a operação da lista permitida chega ao supervisor")
        self.assertIn("Pedido enviado", self.last())
        self.assertEqual(self.bot.check_pending_results(), 0, "sem resultado ainda: não inventa sucesso")
        control.write_result(cdbs.DATA, pending[0]["id"], "tunnel_restart", "ok", "Túnel conectado. Link: https://novo-link.trycloudflare.com")
        fresh = cdbs.bot_mod.Bot(cdbs.DATA / "system", settings=cdbs.get_settings(), ops=cdbs.get_ops(), notifications=cdbs.get_notifications(),
                                 audit=cdbs.get_audit(), sender_factory=lambda: self.sender)      # "o servidor reiniciou": outra instância
        self.assertEqual(fresh.check_pending_results(), 1)
        self.assertIn("✓ Túnel conectado. Link: https://novo-link.trycloudflare.com", self.last())
        self.assertEqual(fresh.check_pending_results(), 0)

    # ----------------------------------------------------------------- robustez
    def test_duplicates_late_messages_rate_limit_and_provider_failure(self):
        message = self.msg("!Status", message_id="wamid.repetida")
        self.assertEqual(self.bot.process(message), "status")
        self.assertEqual(self.bot.process(message), "duplicada")
        again = cdbs.bot_mod.Bot(cdbs.DATA / "system", settings=cdbs.get_settings(), ops=cdbs.get_ops(), notifications=cdbs.get_notifications(),
                                 audit=cdbs.get_audit(), sender_factory=lambda: self.sender)
        self.assertEqual(again.process(message), "duplicada", "o reenvio da Meta depois de um reinício também é ignorado")
        self.assertEqual(self.say("!Link 6", age=900), "atrasada")
        self.assertIn("mais de 10 minutos de atraso", self.last())
        self.bot.per_minute = 3
        results = [self.say("!Status") for _ in range(5)]
        self.assertEqual(results[:2], ["status", "status"])
        self.assertEqual(results[-1], "limite")
        warnings = [t for t in self.sender.texts() if t.startswith("Muitos comandos")]
        self.assertEqual(len(warnings), 1, "avisa do limite uma vez, sem virar spam")
        self.bot.per_minute = 100
        self.clock[0] += 61
        self.sender.fail = WhatsAppError("ERRO_META", "fora do ar", retryable=True)
        self.assertEqual(self.say("!Ajuda"), "ajuda", "a Meta fora do ar não derruba o bot")
        self.assertIn("resposta_falhou", [e["action"] for e in cdbs.get_audit().query(category="bot")])
        self.sender.fail = None
        self.assertEqual(cdbs.get_bot().status()["ready"], False, "sem token da API o bot não está pronto (e diz por quê)")


if __name__ == "__main__":
    unittest.main()
