"""3.0 / Parte 2.3 — configurações e cliente da API oficial do WhatsApp, contra um servidor FALSO da Meta (localhost).
Nenhum teste envia mensagem de verdade nem usa a internet."""
import contextlib
import hashlib
import hmac
import json
import stat
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import settings as settings_mod
import whatsapp

TOKEN = "EAAGtokenFALSOparaTESTE0123456789abcdefABCDEF"


class FakeMeta:
    """Imita POST /<versão>/<phone-id>/messages. `script` decide a resposta de cada pedido."""

    def __init__(self):
        self.requests: list[dict] = []
        self.script = []                      # lista de (status, corpo) ou "lento"
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                return

            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length) or b"{}")
                outer.requests.append({"path": self.path, "auth": self.headers.get("Authorization", ""), "body": body})
                step = outer.script.pop(0) if outer.script else (200, {"messaging_product": "whatsapp", "messages": [{"id": f"wamid.TESTE{len(outer.requests)}"}]})
                if step == "lento":
                    time.sleep(3)
                    step = (200, {"messages": [{"id": "wamid.lento"}]})
                status, payload = step
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@contextlib.contextmanager
def meta():
    fake = FakeMeta()
    try:
        yield fake
    finally:
        fake.close()


def error_body(code, message="erro", subcode=None):
    return {"error": {"message": message, "type": "OAuthException", "code": code, **({"error_subcode": subcode} if subcode else {}), "fbtrace_id": "x"}}


class SettingsTests(unittest.TestCase):
    def make(self, environ=None):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        return settings_mod.Settings(Path(self.folder.name), environ=environ or {})

    def test_defaults_validation_and_persistence(self):
        st = self.make()
        data = st.get()
        self.assertEqual(data["monitor"]["interval_minutes"], 30)
        self.assertFalse(data["notifications"]["enabled"])
        self.assertEqual(data["whatsapp"]["api_version"], "v26.0")
        st.update("monitor", {"interval_minutes": 45, "public_check": True})
        self.assertEqual(st.get()["monitor"]["interval_minutes"], 45)
        for section, changes in (("monitor", {"interval_minutes": 2}), ("monitor", {"interval_minutes": "x"}), ("monitor", {"nao_existe": 1}),
                                 ("whatsapp", {"template_report": "Nome Com Espaço"}), ("whatsapp", {"api_version": "26"}),
                                 ("tunnel", {"mode": "ssh"}), ("secao", {"a": 1}), ("notifications", {"recipients": ["123"]}),
                                 ("notifications", {"recipients": [f"+55 85 9{i:04d}-0000" for i in range(6)]})):
            with self.subTest(section=section, changes=changes), self.assertRaises(settings_mod.SettingsError):
                st.update(section, changes)
        st.update("notifications", {"recipients": "+55 (85) 99999-8888, 85 98888-7777, 0055 11 97777-6666"})
        self.assertEqual(st.get()["notifications"]["recipients"], ["5585999998888", "5585988887777", "5511977776666"])
        self.assertEqual(stat.S_IMODE((Path(self.folder.name) / "settings.json").stat().st_mode), 0o600)
        view = st.view()
        self.assertNotIn("recipients", view["settings"]["notifications"])
        self.assertEqual(view["settings"]["notifications"]["recipients_view"][0], "+55•••••••8888")

    def test_env_wins_and_locks_the_field(self):
        st = self.make({"CDBS_MONITOR_INTERVAL_MINUTES": "60", "CDBS_WHATSAPP_RECIPIENT": "+5585999998888", "CDBS_MONITOR_PUBLIC_CHECK": "lixo"})
        self.assertEqual(st.get()["monitor"]["interval_minutes"], 60)
        self.assertIn("monitor.interval_minutes", st.locked_by_env())
        with self.assertRaises(settings_mod.SettingsError) as caught:
            st.update("monitor", {"interval_minutes": 15})
        self.assertEqual(caught.exception.status, 409)
        self.assertFalse(st.get()["monitor"]["public_check"], "valor ruim no .env não quebra: vale o padrão/painel")

    def test_secrets_are_write_only(self):
        st = self.make()
        st.set_secret("whatsapp_token", TOKEN)
        st.set_secret("whatsapp_phone_number_id", "123456789012345")
        self.assertEqual(st.secret("whatsapp_token"), TOKEN)
        dump = json.dumps(st.view())
        self.assertNotIn(TOKEN, dump)
        self.assertNotIn("123456789012345", dump)
        self.assertTrue(st.secrets_view()["whatsapp_token"]["configured"])
        self.assertEqual(st.secrets_view()["whatsapp_token"]["hint"], "••••CDEF")
        self.assertEqual(stat.S_IMODE((Path(self.folder.name) / "secrets.json").stat().st_mode), 0o600)
        for bad in ("", "com espaço", "x" * 3000, 123):
            with self.subTest(bad=bad), self.assertRaises(settings_mod.SettingsError):
                st.set_secret("whatsapp_token", bad)
        with self.assertRaises(settings_mod.SettingsError):
            st.set_secret("whatsapp_phone_number_id", "abc123")
        with self.assertRaises(settings_mod.SettingsError):
            st.set_secret("nao_existe", "x")
        self.assertEqual(st.whatsapp_ready(), (True, ""))
        st.clear_secret("whatsapp_token")
        self.assertFalse(st.whatsapp_ready()[0])
        token = st.generate_verify_token()
        self.assertTrue(token.startswith("cdbs-") and len(token) > 20)
        self.assertNotIn(token, json.dumps(st.view()))
        env = self.make({"CDBS_WHATSAPP_ACCESS_TOKEN": "do-env"})
        self.assertEqual(env.secret("whatsapp_token"), "do-env")
        self.assertEqual(env.secrets_view()["whatsapp_token"]["source"], "env")
        with self.assertRaises(settings_mod.SettingsError):
            env.set_secret("whatsapp_token", "outro")

    def test_phone_normalization(self):
        n = settings_mod.normalize_phone
        self.assertEqual(n("+55 85 99999-8888"), "5585999998888")
        self.assertEqual(n("(85) 99999-8888"), "5585999998888")
        self.assertEqual(n("+1 650 555 1234"), "16505551234")
        for bad in ("", "123", "+0 123 456 789", "abc", None, "1" * 16):
            with self.subTest(bad=bad), self.assertRaises(settings_mod.SettingsError):
                n(bad)


class CloudApiTests(unittest.TestCase):
    def client(self, fake, **kw):
        return whatsapp.CloudAPI("123456789", TOKEN, base=fake.base, **kw)

    def test_payload_shapes_and_auth_header(self):
        with meta() as fake:
            api = self.client(fake)
            self.assertEqual(api.send_text("5585999998888", "olá")["message_id"], "wamid.TESTE1")
            api.send_template("5585999998888", "cdbs_relatorio_sites", "pt_BR", ["linha\ncom\tquebras", "a      b", ""])
            api.send_list("5585999998888", "Escolha", "Ver opções que passam do limite", [("1", "Listar as 10 posições e seus estados", "x" * 100)])
            api.send_buttons("5585999998888", "Confirmar?", [("sim", "SIM"), ("nao", "CANCELAR")])
            first, tpl, lst, btn = fake.requests
            self.assertEqual(first["path"], "/v26.0/123456789/messages")
            self.assertEqual(first["auth"], f"Bearer {TOKEN}")
            self.assertEqual(first["body"]["type"], "text")
            params = [p["text"] for p in tpl["body"]["template"]["components"][0]["parameters"]]
            self.assertEqual(params, ["linha · com · quebras", "a   b", "—"])
            self.assertTrue(all("\n" not in p and "\t" not in p and "    " not in p for p in params))
            self.assertEqual(tpl["body"]["template"]["language"]["code"], "pt_BR")
            row = lst["body"]["interactive"]["action"]["sections"][0]["rows"][0]
            self.assertLessEqual(len(row["title"]), 24)
            self.assertLessEqual(len(row["description"]), 72)
            self.assertLessEqual(len(lst["body"]["interactive"]["action"]["button"]), 20)
            self.assertEqual([b["reply"]["title"] for b in btn["body"]["interactive"]["action"]["buttons"]], ["SIM", "CANCELAR"])

    def test_error_mapping_retryable_and_no_token_leak(self):
        cases = [(401, error_body(190, f"Invalid OAuth access token {TOKEN}"), "TOKEN_INVALIDO", False),
                 (400, error_body(131047, "Re-engagement message"), "JANELA_FECHADA", False),
                 (404, error_body(132001, "Template name does not exist"), "MODELO_INEXISTENTE", False),
                 (400, error_body(132018, "param"), "MODELO_PARAMETROS", False),
                 (429, error_body(130429, "Rate limit hit"), "LIMITE", True),
                 (400, error_body(131030, "Recipient not in allowed list"), "DESTINATARIO_NAO_PERMITIDO", False),
                 (500, {"error": {"message": "interno"}}, "ERRO_META", True),
                 (503, {}, "ERRO_META", True),
                 (418, {"error": {"code": 999999}}, "ERRO_API", False),
                 (200, {"messages": []}, "RESPOSTA_INVALIDA", False)]
        with meta() as fake:
            api = self.client(fake)
            for status, body, code, retry in cases:
                fake.script.append((status, body))
                with self.subTest(code=code), self.assertRaises(whatsapp.WhatsAppError) as caught:
                    api.send_text("5585999998888", "x")
                self.assertEqual(caught.exception.code, code)
                self.assertEqual(caught.exception.retryable, retry)
                self.assertNotIn(TOKEN, str(caught.exception))
                self.assertNotIn(TOKEN, json.dumps(caught.exception.as_dict()))
            self.assertNotIn(TOKEN, repr(api))

    def test_timeout_and_no_network(self):
        with meta() as fake:
            fake.script.append("lento")
            api = self.client(fake, timeout=0.5)
            with self.assertRaises(whatsapp.WhatsAppError) as caught:
                api.send_text("5585999998888", "x")
            self.assertEqual(caught.exception.code, "TIMEOUT")
            self.assertTrue(caught.exception.retryable)
            dead = fake.base
        offline = whatsapp.CloudAPI("123456789", TOKEN, base=dead)
        with self.assertRaises(whatsapp.WhatsAppError) as caught:
            offline.send_text("5585999998888", "x")
        self.assertEqual(caught.exception.code, "SEM_REDE")
        self.assertTrue(caught.exception.retryable)

    def test_configuration_is_validated(self):
        for kwargs in ({"phone_number_id": "", "token": TOKEN}, {"phone_number_id": "12a", "token": TOKEN}, {"phone_number_id": "123", "token": ""}):
            with self.subTest(kwargs=kwargs), self.assertRaises(whatsapp.WhatsAppError):
                whatsapp.CloudAPI(**kwargs)
        for base in ("http://graph.facebook.com", "ftp://x", "https://user:pw@x.com"):
            with self.subTest(base=base), self.assertRaises(whatsapp.WhatsAppError):
                whatsapp.CloudAPI("123", TOKEN, base=base)
        with self.assertRaises(whatsapp.WhatsAppError):
            whatsapp.CloudAPI("123", TOKEN, api_version="latest")


class WebhookParsingTests(unittest.TestCase):
    def test_signature(self):
        body = b'{"object":"whatsapp_business_account"}'
        good = "sha256=" + hmac.new(b"segredo-do-app", body, hashlib.sha256).hexdigest()
        self.assertTrue(whatsapp.verify_signature("segredo-do-app", body, good))
        self.assertTrue(whatsapp.verify_signature("segredo-do-app", body, good.upper().replace("SHA256=", "sha256=")))
        for header in (None, "", "sha1=abc", good[:-2] + "00", "sha256="):
            self.assertFalse(whatsapp.verify_signature("segredo-do-app", body, header))
        self.assertFalse(whatsapp.verify_signature("", body, good))
        self.assertFalse(whatsapp.verify_signature("outro", body, good))
        self.assertFalse(whatsapp.verify_signature("segredo-do-app", body + b" ", good))

    def test_handshake(self):
        ok = {"hub.mode": ["subscribe"], "hub.verify_token": ["meu-token"], "hub.challenge": ["1158201444"]}
        self.assertEqual(whatsapp.handshake(ok, "meu-token"), "1158201444")
        self.assertIsNone(whatsapp.handshake({**ok, "hub.verify_token": ["errado"]}, "meu-token"))
        self.assertIsNone(whatsapp.handshake({**ok, "hub.mode": ["unsubscribe"]}, "meu-token"))
        self.assertIsNone(whatsapp.handshake(ok, ""))
        self.assertIsNone(whatsapp.handshake({**ok, "hub.challenge": ["<script>"]}, "meu-token"))

    def test_parse_messages_replies_and_statuses(self):
        payload = {"object": "whatsapp_business_account", "entry": [{"id": "WABA", "changes": [{"field": "messages", "value": {
            "messaging_product": "whatsapp", "metadata": {"phone_number_id": "123"},
            "messages": [
                {"from": "5585999998888", "id": "wamid.A", "timestamp": "1761480000", "type": "text", "text": {"body": "!Link"}},
                {"from": "5585999998888", "id": "wamid.B", "timestamp": "1761480001", "type": "interactive", "interactive": {"type": "list_reply", "list_reply": {"id": "op1", "title": "Listar"}}},
                {"from": "5585999998888", "id": "wamid.C", "timestamp": "1761480002", "type": "interactive", "interactive": {"type": "button_reply", "button_reply": {"id": "sim", "title": "SIM"}}},
                {"from": "5585999998888", "id": "wamid.D", "timestamp": "x", "type": "image", "image": {}},
                "lixo"],
            "statuses": [{"id": "wamid.TESTE1", "status": "delivered", "recipient_id": "5585999998888", "timestamp": "1761480010"},
                         {"id": "wamid.TESTE2", "status": "failed", "recipient_id": "5585999998888", "errors": [{"code": 131026, "title": "Undeliverable"}]}]}}]}]}
        items = whatsapp.parse_webhook(payload)
        kinds = [(i.kind, i.message_id) for i in items]
        self.assertEqual(kinds, [("message", "wamid.A"), ("message", "wamid.B"), ("message", "wamid.C"), ("message", "wamid.D"),
                                 ("status", "wamid.TESTE1"), ("status", "wamid.TESTE2")])
        self.assertEqual(items[0].text, "!Link")
        self.assertEqual(items[0].timestamp, 1761480000)
        self.assertEqual(items[1].reply_id, "op1")
        self.assertEqual(items[2].reply_id, "sim")
        self.assertEqual(items[3].timestamp, 0)
        self.assertEqual(items[5].error["code"], 131026)
        self.assertEqual(whatsapp.parse_webhook({"object": "page"}), [])
        self.assertEqual(whatsapp.parse_webhook("lixo"), [])


if __name__ == "__main__":
    unittest.main()
