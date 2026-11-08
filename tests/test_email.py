"""3.0 — avisos por E-MAIL (SMTP): configuração validada, senha só-gravação, envio de verdade para um servidor SMTP DE
MENTIRA (127.0.0.1), senha errada explicada sem repetir, servidor fora do ar com tentativas limitadas, WhatsApp e e-mail
juntos, teste só por e-mail e a rota do painel. Nada sai para a internet."""
import base64
import json
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import audit
import backup
import cdbs
import emailer
import notify
import settings as settings_mod
from test_catalog import request, stack

USER = "dono.cdbs@gmail.com"
PASSWORD = "abcd efgh ijkl mnop"                 # formato da senha de app do Gmail (falsa)
TOKEN = "EAAGtokenFALSOparaTESTE0123456789abcdefABCDEF"


class FakeSMTP:
    """Servidor SMTP mínimo, sem criptografia, só em 127.0.0.1: guarda o que recebeu e confere usuário/senha."""

    def __init__(self, user=USER, password=PASSWORD, refuse=()):
        self.user, self.password, self.refuse = user, password, set(refuse)
        self.messages, self.logins = [], []
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(5)
        self.port = self.sock.getsockname()[1]
        self._stop = False
        threading.Thread(target=self._serve, daemon=True).start()

    def close(self):
        self._stop = True
        self.sock.close()

    def _serve(self):
        while not self._stop:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._session, args=(conn,), daemon=True).start()

    def _session(self, conn):
        f = conn.makefile("rb")
        send = lambda line: conn.sendall(line.encode() + b"\r\n")   # noqa: E731
        send("220 fake ESMTP")
        mail = {"to": []}
        try:
            while True:
                raw = f.readline()
                if not raw:
                    return
                line = raw.decode("utf-8", "replace").rstrip("\r\n")
                cmd = line[:4].upper()
                if cmd in ("EHLO", "HELO"):
                    send("250-fake")
                    send("250-AUTH PLAIN LOGIN")
                    send("250 OK")
                elif cmd == "AUTH":
                    parts = line.split()
                    if len(parts) < 3 or parts[1].upper() != "PLAIN":
                        send("535 5.7.8 credenciais recusadas")                 # só PLAIN (o smtplib tenta LOGIN depois)
                        continue
                    _, user, password = base64.b64decode(parts[2]).decode().split("\0")
                    self.logins.append(user)
                    send("235 ok" if (user, password) == (self.user, self.password) else "535 5.7.8 credenciais recusadas")
                elif cmd == "MAIL":
                    mail = {"from": line, "to": []}
                    send("250 ok")
                elif cmd == "RCPT":
                    address = line.split("<", 1)[1].split(">", 1)[0]
                    if address in self.refuse:
                        send("550 caixa inexistente")
                    else:
                        mail["to"].append(address)
                        send("250 ok")
                elif cmd == "DATA":
                    send("354 manda")
                    body = []
                    while True:
                        chunk = f.readline().decode("utf-8", "replace")
                        if chunk.rstrip("\r\n") == ".":
                            break
                        body.append(chunk)
                    mail["data"] = "".join(body)
                    self.messages.append(mail)
                    send("250 guardado")
                elif cmd == "QUIT":
                    send("221 tchau")
                    return
                else:
                    send("250 ok")
        finally:
            conn.close()


class Env:
    def __init__(self, test, *, smtp=None, whatsapp=False):
        folder = tempfile.TemporaryDirectory()
        test.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.settings = settings_mod.Settings(self.root / "system", environ={})
        if smtp is not None:
            self.settings.update("email", {"enabled": True, "smtp_host": "127.0.0.1", "smtp_port": smtp.port, "smtp_security": "none",
                                           "smtp_user": USER, "recipients": [USER]})
            self.settings.set_secret("email_password", PASSWORD)
        self.wa = []
        if whatsapp:
            self.settings.set_secret("whatsapp_phone_number_id", "123456789")
            self.settings.set_secret("whatsapp_token", TOKEN)
            self.settings.update("notifications", {"enabled": True, "recipients": ["+55 85 99999-8888"]})
        self.clock = [1_761_480_000.0]
        outer = self

        class WA:
            def send_template(self, to, name, language, params):
                outer.wa.append(to)
                return {"message_id": f"wamid.{len(outer.wa)}"}

            send_text = lambda self, to, body: WA.send_template(self, to, "", "", [])   # noqa: E731

        def mailer():
            mail = self.settings.get()["email"]
            return emailer.SMTPSender(mail["smtp_host"], mail["smtp_port"], mail["smtp_security"], mail["smtp_user"],
                                      self.settings.secret("email_password"), mail["from_address"] or mail["smtp_user"], timeout=3)

        self.audit = audit.AuditLog(self.root / "system", clock=lambda: self.clock[0])
        self.center = notify.NotificationCenter(self.root / "notifications", self.settings, sender_factory=WA, audit=self.audit,
                                                clock=lambda: self.clock[0], mailer_factory=mailer)

    def last(self):
        return self.center.list(1)[0]


class EmailTests(unittest.TestCase):
    def smtp(self, **kw):
        server = FakeSMTP(**kw)
        self.addCleanup(server.close)
        return server

    def test_settings_are_validated_and_the_password_is_write_only(self):
        config_ = settings_mod.Settings(Path(tempfile.mkdtemp()) / "system", environ={})
        for change in ({"recipients": ["nao-e-email"]}, {"smtp_port": 0}, {"smtp_security": "nenhuma"}, {"smtp_host": "smtp gmail"},
                       {"from_address": "x@"}):
            with self.subTest(change=change), self.assertRaises(settings_mod.SettingsError):
                config_.update("email", change)
        config_.update("email", {"enabled": True, "smtp_user": USER, "recipients": "Dono.CDBS@gmail.com, outro@exemplo.com.br"})
        self.assertEqual(config_.get()["email"]["recipients"], [USER, "outro@exemplo.com.br"])
        self.assertEqual(config_.get()["email"]["smtp_host"], "", "sem servidor ainda")
        self.assertEqual(config_.email_ready(), (False, "falta o servidor SMTP"))
        config_.update("email", {"smtp_host": "smtp.gmail.com"})
        self.assertIn("senha", config_.email_ready()[1])
        config_.set_secret("email_password", PASSWORD)              # com espaços, como o Gmail mostra
        self.assertEqual(config_.email_ready(), (True, ""))
        view = json.dumps(config_.view(), ensure_ascii=False)
        self.assertNotIn(PASSWORD, view)
        self.assertNotIn("dono.cdbs@gmail.com\"", view.replace("smtp_user\": \"dono.cdbs@gmail.com\"", ""), "destinatários aparecem mascarados")
        self.assertIn("do•••@gmail.com", view)

    def test_sender_never_sends_unencrypted_outside_this_device(self):
        with self.assertRaises(emailer.EmailError):
            emailer.SMTPSender("smtp.gmail.com", 25, "none", USER, PASSWORD, USER)
        with self.assertRaises(emailer.EmailError):
            emailer.SMTPSender("smtp.gmail.com", 465, "ssl", USER, "", USER)
        self.assertEqual(repr(emailer.SMTPSender("smtp.gmail.com", 465, "ssl", USER, PASSWORD, USER)), "SMTPSender(<configurado>)")

    def test_alert_is_delivered_by_email_and_secrets_stay_out_of_records(self):
        server = self.smtp()
        env = Env(self, smtp=server)
        event = env.center.emit("alerta", "posicao:2:loja", "Posição 2 (loja): 🔴 Fora do ar", "CDBS.OS — mudança detectada\n2. loja: Ativo → Fora do ar")
        self.assertEqual(event["status"], "pendente")
        self.assertEqual(env.center.deliver_pending(), 1)
        self.assertEqual(env.last()["deliveries"][0]["status"], "enviado")
        self.assertEqual(server.logins, [USER])
        message = server.messages[0]
        self.assertEqual(message["to"], [USER])
        self.assertIn("Subject: CDBS.OS =?utf-8?", message["data"])   # assunto com acento vai codificado
        self.assertIn("mudan", message["data"])
        records = (env.root / "notifications" / "events.json").read_text() + (env.root / "system" / "audit.jsonl").read_text()
        self.assertNotIn(PASSWORD, records)
        self.assertNotIn(USER, records, "o endereço completo não fica nos registros")
        self.assertEqual(env.center.email_status()["state"], "pronto")

    def test_wrong_password_is_explained_and_not_retried(self):
        server = self.smtp(password="outra senha qualquer")
        env = Env(self, smtp=server)
        env.center.emit("alerta", "x:1", "Teste", "x")
        env.center.deliver_pending()
        delivery = env.last()["deliveries"][0]
        self.assertEqual((delivery["status"], delivery["attempts"]), ("falhou", 1))
        self.assertIn("LOGIN_RECUSADO", delivery["error"])
        self.assertIn("SENHA DE APP", delivery["error"])
        env.clock[0] += 10_000
        self.assertEqual(env.center.deliver_pending(), 0, "senha errada não fica tentando")
        self.assertEqual(env.center.email_status()["state"], "ultimo_envio_falhou")

    def test_server_down_is_retried_a_limited_number_of_times(self):
        server = self.smtp()
        env = Env(self, smtp=server)
        with socket.socket() as probe:                               # porta sem ninguém escutando: conexão recusada
            probe.bind(("127.0.0.1", 0))
            closed = probe.getsockname()[1]
        env.settings.update("email", {"smtp_port": closed})
        env.center.emit("alerta", "x:2", "Teste", "x")
        env.center.deliver_pending()
        self.assertEqual(env.last()["deliveries"][0]["status"], "pendente")
        for wait in notify.RETRY_DELAYS:
            env.clock[0] += wait
            env.center.deliver_pending()
        delivery = env.last()["deliveries"][0]
        self.assertEqual((delivery["status"], delivery["attempts"]), ("falhou", 3))

    def test_whatsapp_and_email_together_and_test_by_email_only(self):
        server = self.smtp()
        env = Env(self, smtp=server, whatsapp=True)
        env.center.emit("alerta", "x:3", "Teste", "x", params=["Teste", "—", "agora"])
        self.assertEqual(sorted(d["via"] for d in env.last()["deliveries"]), ["email", "whatsapp"])
        env.center.deliver_pending()
        self.assertEqual(len(env.wa), 1)
        self.assertEqual(len(server.messages), 1)
        test = env.center.send_test("relatório", ["a"] * 12, only="email")
        self.assertEqual([d["via"] for d in test["deliveries"]], ["email"])
        env.center.deliver_pending()
        self.assertEqual(len(env.wa), 1, "teste só por e-mail não manda WhatsApp")
        self.assertEqual(len(server.messages), 2)
        refused = self.smtp(refuse=[USER])
        env.settings.update("email", {"smtp_port": refused.port})
        env.center.emit("alerta", "x:4", "Teste", "x")
        env.center.deliver_pending()
        mail = [d for d in env.last()["deliveries"] if d["via"] == "email"][0]
        self.assertEqual(mail["status"], "falhou")
        self.assertIn("DESTINO_RECUSADO", mail["error"])

    def test_nothing_configured_stays_local(self):
        env = Env(self)
        event = env.center.emit("alerta", "x:5", "Teste", "x")
        self.assertEqual(event["status"], "desativado")
        self.assertIn("e-mail desligado", event["note"])


class EmailPanelTests(unittest.TestCase):
    def test_panel_saves_email_settings_and_sends_a_test_by_email(self):
        server = FakeSMTP()
        self.addCleanup(server.close)
        with stack() as urls, patch.object(cdbs, "SYSTEM_GATE", backup.RateGate(10_000, 60)), \
                patch.object(cdbs, "SITE_PORT", int(urls["public"].rsplit(":", 1)[1])):
            base = urls["admin"]
            status, headers, _ = request(base + "/api/setup", "POST", json_body={"password": "senha-do-admin-123"}, headers={"Origin": base})
            sent = {"Cookie": headers["set-cookie"].split(";", 1)[0], "Origin": base, "X-CDBS-Request": "1"}
            call = lambda method, path, body=None: request(base + path, method, json_body=body, headers=sent)   # noqa: E731
            self.assertEqual(call("POST", "/api/settings/email", {"enabled": True, "smtp_host": "127.0.0.1", "smtp_port": server.port,
                                                                  "smtp_security": "none", "smtp_user": USER, "recipients": [USER]})[0], 200)
            self.assertEqual(call("POST", "/api/secrets", {"name": "email_password", "value": PASSWORD})[0], 200)
            status, _, body = call("GET", "/api/settings")
            self.assertNotIn(PASSWORD, body.decode())
            self.assertTrue(json.loads(body)["secrets"]["email_password"]["configured"])
            self.assertEqual(call("POST", "/api/notifications/test", {"confirm": True, "channel": "sms"})[0], 400)
            status, _, body = call("POST", "/api/notifications/test", {"confirm": True, "channel": "email"})
            self.assertEqual(status, 202, body)
            center = cdbs.get_notifications()
            center.deliver_pending()
            deadline = time.time() + 10
            while time.time() < deadline and not server.messages:
                time.sleep(0.05)
            self.assertEqual(len(server.messages), 1)
            self.assertIn("mensagem de teste", server.messages[0]["data"])
            status, _, body = call("GET", "/api/notifications")
            data = json.loads(body)
            self.assertEqual(data["items"][0]["deliveries"][0]["status"], "enviado")
            self.assertEqual(data["status"]["email"]["state"], "pronto")


if __name__ == "__main__":
    unittest.main()
