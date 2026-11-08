"""3.0 / Parte 2.3 — central de notificações: histórico local primeiro, entrega em segundo plano, sem spam (agrupamento,
espera, recuperação), tentativas limitadas, janela de 24 h, confirmações de entrega e integração com o histórico."""
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import audit
import notify
import settings as settings_mod
from whatsapp import WhatsAppError

TOKEN = "EAAGtokenFALSOparaTESTE0123456789abcdefABCDEF"
OWNER = "5585999998888"


class FakeSender:
    def __init__(self):
        self.sent = []
        self.fail_with = []                     # WhatsAppError a lançar nas próximas chamadas

    def _next(self, kind, to, payload):
        if self.fail_with:
            raise self.fail_with.pop(0)
        self.sent.append((kind, to, payload))
        return {"message_id": f"wamid.{len(self.sent)}"}

    def send_text(self, to, body):
        return self._next("texto", to, body)

    def send_template(self, to, name, language, params):
        return self._next("modelo", to, {"name": name, "language": language, "params": params})


class Env:
    def __init__(self, test, *, enabled=True, configured=True, recipients=(OWNER,)):
        self.folder = tempfile.TemporaryDirectory()
        test.addCleanup(self.folder.cleanup)
        root = Path(self.folder.name)
        self.settings = settings_mod.Settings(root / "system", environ={})
        if configured:
            self.settings.set_secret("whatsapp_phone_number_id", "123456789")
            self.settings.set_secret("whatsapp_token", TOKEN)
        self.settings.update("notifications", {"enabled": enabled, "recipients": list(recipients)})
        self.clock = [1_761_480_000.0]
        self.audit = audit.AuditLog(root / "system", clock=lambda: self.clock[0])
        self.sender = FakeSender()
        self.factory_error = None

        def factory():
            if self.factory_error:
                raise self.factory_error
            return self.sender

        self.center = notify.NotificationCenter(root / "notifications", self.settings, sender_factory=factory, audit=self.audit,
                                                clock=lambda: self.clock[0])
        self.root = root

    def last(self):
        return self.center.list(1)[0]


class LocalFirstTests(unittest.TestCase):
    def test_disabled_or_unconfigured_stays_local_and_nothing_is_sent(self):
        for kwargs, note in (({"enabled": False}, "WhatsApp desligado"), ({"configured": False}, "sem configuração"), ({"recipients": ()}, "nenhum número")):
            with self.subTest(kwargs=kwargs):
                env = Env(self, **kwargs)
                event = env.center.emit("alerta", "x:1", "Teste", "texto", category="sistema")
                self.assertEqual(event["status"], "desativado")
                self.assertIn(note, event["note"])
                self.assertEqual(env.center.deliver_pending(), 0)
                self.assertEqual(env.sender.sent, [])
                self.assertEqual(len(env.center.list()), 1, "o evento fica no histórico local mesmo sem WhatsApp")

    def test_template_outside_window_text_inside_and_blocked_without_template(self):
        env = Env(self)
        env.center.emit("alerta", "a:1", "Site caiu", "CDBS.OS — site caiu\nlinha 2", params=["Site caiu", "HTTP 404", "26/10 10:30"])
        self.assertEqual(env.center.deliver_pending(), 1)
        kind, to, payload = env.sender.sent[-1]
        self.assertEqual((kind, to), ("modelo", OWNER))
        self.assertEqual(payload["name"], "cdbs_alerta_site")
        self.assertEqual(len(payload["params"]), 3)
        env.center.record_inbound(OWNER)                                     # o dono mandou mensagem: janela de 24 h aberta
        env.center.emit("alerta", "a:2", "Outro", "texto livre\ncom linhas")
        env.center.deliver_pending()
        self.assertEqual(env.sender.sent[-1][:2], ("texto", OWNER))
        self.assertEqual(env.sender.sent[-1][2], "texto livre\ncom linhas")
        env.clock[0] += 25 * 3600                                            # janela fechou
        env.settings.update("whatsapp", {"template_alert": ""})
        env.center.emit("alerta", "a:3", "Sem modelo", "x")
        env.center.deliver_pending()
        last = env.last()
        self.assertEqual(last["status"], "bloqueado")
        self.assertIn("sem modelo", last["deliveries"][0]["error"])
        raw = (env.root / "notifications" / "events.json").read_text()
        self.assertNotIn(TOKEN, raw)
        self.assertNotIn(OWNER, raw, "o número completo não fica no arquivo de eventos")

    def test_retries_are_limited_and_only_for_temporary_errors(self):
        env = Env(self)
        env.sender.fail_with = [WhatsAppError("SEM_REDE", "sem rede", retryable=True)] * 5
        env.center.emit("alerta", "r:1", "Teste", "x")
        env.center.deliver_pending()
        delivery = env.last()["deliveries"][0]
        self.assertEqual((delivery["status"], delivery["attempts"]), ("pendente", 1))
        self.assertEqual(env.center.deliver_pending(), 0, "antes da espera não tenta de novo")
        for wait in notify.RETRY_DELAYS:
            env.clock[0] += wait
            env.center.deliver_pending()
        delivery = env.last()["deliveries"][0]
        self.assertEqual((delivery["status"], delivery["attempts"]), ("falhou", 3))
        env.clock[0] += 10_000
        self.assertEqual(env.center.deliver_pending(), 0, "falhou é definitivo: sem laço de tentativas")
        env.sender.fail_with = [WhatsAppError("TOKEN_INVALIDO", "token expirou", retryable=False)]
        env.center.emit("alerta", "r:2", "Teste 2", "x")
        env.center.deliver_pending()
        delivery = env.last()["deliveries"][0]
        self.assertEqual((delivery["status"], delivery["attempts"]), ("falhou", 1))
        self.assertIn("TOKEN_INVALIDO", delivery["error"])
        self.assertEqual(env.center.status()["state"], "ultimo_envio_falhou")
        env.factory_error = WhatsAppError("SEM_CONFIG", "Falta o token")
        env.center.emit("alerta", "r:3", "Teste 3", "x")
        env.center.deliver_pending()
        self.assertEqual(env.last()["status"], "bloqueado")
        failed = env.audit.query(category="notificacao", result="falha")
        self.assertTrue(failed)

    def test_window_that_closed_falls_back_to_template(self):
        env = Env(self)
        env.center.record_inbound(OWNER)
        env.sender.fail_with = [WhatsAppError("JANELA_FECHADA", "24 h", retryable=False)]
        env.center.emit("alerta", "w:1", "Teste", "x", params=["Teste", "—", "agora"])
        env.center.deliver_pending()
        self.assertEqual(env.last()["deliveries"][0]["status"], "pendente")
        env.clock[0] += notify.RETRY_DELAYS[0]
        env.center.deliver_pending()
        self.assertEqual(env.sender.sent[-1][0], "modelo")
        self.assertEqual(env.last()["deliveries"][0]["status"], "aceito")


class AntiSpamTests(unittest.TestCase):
    def test_cooldown_grouping_and_recovery_only_after_alert(self):
        env = Env(self)
        first = env.center.emit("alerta", "tunel:tunel_caiu:", "Túnel caiu", "x")
        again = env.center.emit("alerta", "tunel:tunel_caiu:", "Túnel caiu", "x")
        self.assertEqual(first["status"], "pendente")
        self.assertEqual(again["status"], "agrupado")
        self.assertIn("já avisado", again["note"])
        env.center.deliver_pending()
        self.assertEqual(len(env.sender.sent), 1)
        back = env.center.emit("recuperacao", "tunel:tunel_conectado:", "Túnel voltou", "x")
        self.assertEqual(back["status"], "pendente", "voltou ao normal é avisado porque a queda foi avisada")
        quiet = env.center.emit("recuperacao", "tunel:tunel_conectado:", "Túnel voltou", "x")
        self.assertEqual(quiet["status"], "registrado")
        self.assertIn("nenhum alerta", quiet["note"])
        env.clock[0] += 3601
        self.assertEqual(env.center.emit("alerta", "tunel:tunel_caiu:", "Túnel caiu", "x")["status"], "pendente")
        env.settings.update("notifications", {"alerts_enabled": False})
        self.assertEqual(env.center.emit("alerta", "outro:x", "Outro", "x")["status"], "registrado")

    def test_periodic_report_is_one_per_boundary_and_changes_become_one_grouped_message(self):
        env = Env(self)
        report = {"started_at": 1_761_480_000, "boundary": 1_761_480_000, "slots": [{"slot": i, "slug": None, "state": "VAGO"} for i in range(1, 11)],
                  "summary": {"text": "10 vagos", "counts": {"VAGO": 10}}, "public_check": False, "tunnel": {}}
        env.center.on_round(report, [], "periodico", next_at=1_761_481_800)
        env.center.on_round(report, [], "periodico", next_at=1_761_481_800)
        reports = env.center.list(kind="relatorio")
        self.assertEqual(len(reports), 1, "reinício no mesmo horário não duplica o relatório")
        env.center.deliver_pending()
        kind, _, payload = env.sender.sent[-1]
        self.assertEqual(kind, "modelo")
        self.assertEqual(payload["name"], "cdbs_relatorio_sites")
        self.assertEqual(len(payload["params"]), 12)
        old = {"slot": 3, "slug": "loja", "state": "ATIVO"}
        new = {"slot": 3, "slug": "loja", "state": "FORA_DO_AR", "reason": "HTTP 500"}
        manual = dict(report, started_at=report["started_at"] + 60)
        env.center.on_round(manual, [(old, new)], "manual")
        alerts = env.center.list(kind="alerta")
        self.assertEqual(alerts[0]["slot"], 3)
        self.assertIn("Fora do ar", alerts[0]["title"])
        env.center.on_round(manual, [(new, dict(new, state="ATIVO", reason=""))], "manual")
        self.assertEqual(env.center.list(kind="recuperacao")[0]["status"], "pendente")
        periodic_change = dict(report, started_at=report["started_at"] + 1800, boundary=report["boundary"] + 1800)
        env.center.on_round(periodic_change, [(old, new)], "periodico")
        self.assertEqual(len(env.center.list(kind="alerta")), 1, "na rodada periódica a mudança vai DENTRO do relatório, sem alerta extra")
        self.assertIn("mudança detectada", env.center.list(kind="relatorio")[0]["text"])

    def test_short_report_template_uses_three_single_line_variables(self):
        # se a Meta recusar o modelo de 12 variáveis, o dono aprova o resumido e escolhe "3" no painel
        env = Env(self)
        with self.assertRaises(settings_mod.SettingsError):
            env.settings.update("whatsapp", {"report_variables": 5})
        env.settings.update("whatsapp", {"report_variables": 3})
        slots = [{"slot": i, "slug": None, "state": "VAGO"} for i in range(1, 11)]
        slots[0] = {"slot": 1, "slug": "adega", "state": "ATIVO"}
        slots[3] = {"slot": 4, "slug": "loja", "state": "FORA_DO_AR"}
        slots[6] = {"slot": 7, "slug": "doces", "state": "PAUSADO"}
        report = {"started_at": 1_761_480_000, "boundary": 1_761_480_000, "slots": slots,
                  "summary": {"text": "1 ativo · 1 fora do ar · 1 pausado · 7 vagos", "counts": {}}, "public_check": False, "tunnel": {}}
        env.center.on_round(report, [], "periodico")
        env.center.deliver_pending()
        kind, _, payload = env.sender.sent[-1]
        self.assertEqual(kind, "modelo")
        self.assertEqual(len(payload["params"]), 3)
        self.assertIn("1 fora do ar", payload["params"][1])
        self.assertIn("4) 🔴 Fora do ar — loja", payload["params"][2])
        self.assertIn("7) ⏸ Pausado — doces", payload["params"][2])
        self.assertNotIn("adega", payload["params"][2], "posição ativa não entra na lista de atenção")
        self.assertTrue(all("\n" not in p and "    " not in p for p in payload["params"]))
        calm = list(report["slots"])
        calm[3], calm[6] = dict(calm[3], state="ATIVO"), dict(calm[6], state="VAGO", slug=None)
        env.center.on_round(dict(report, slots=calm, boundary=report["boundary"] + 1800, started_at=report["started_at"] + 1800), [], "periodico")
        env.center.deliver_pending()
        self.assertIn("nenhuma", env.sender.sent[-1][2]["params"][2])

    def test_report_turned_off_still_alerts_changes_found_by_scheduled_checks(self):
        # o dono desligou o relatório periódico (para gastar menos mensagens) e deixou os alertas ligados
        env = Env(self)
        env.settings.update("notifications", {"report_enabled": False})
        report = {"started_at": 1_761_480_000, "boundary": 1_761_480_000, "slots": [{"slot": i, "slug": None, "state": "VAGO"} for i in range(1, 11)],
                  "summary": {"text": "10 vagos", "counts": {"VAGO": 10}}, "public_check": False, "tunnel": {}}
        old = {"slot": 2, "slug": "adega", "state": "ATIVO"}
        down = {"slot": 2, "slug": "adega", "state": "FORA_DO_AR", "reason": "HTTP 404"}
        env.center.on_round(report, [(old, down)], "periodico")
        self.assertNotEqual(env.center.list(kind="relatorio")[0]["status"], "pendente", "o relatório desligado não é enviado")
        alerts = env.center.list(kind="alerta")
        self.assertEqual(len(alerts), 1, "a queda achada na verificação agendada vira alerta próprio")
        self.assertEqual(alerts[0]["status"], "pendente")
        later = dict(report, started_at=report["started_at"] + 1800, boundary=report["boundary"] + 1800)
        env.center.on_round(later, [(down, dict(down, state="ATIVO", reason=""))], "periodico")
        self.assertEqual(env.center.list(kind="recuperacao")[0]["status"], "pendente", "e a volta é avisada, porque a queda foi")


class IntegrationTests(unittest.TestCase):
    def test_delivery_status_from_webhook_never_regresses(self):
        env = Env(self)
        env.center.emit("alerta", "s:1", "Teste", "x")
        env.center.deliver_pending()
        self.assertEqual(env.last()["status"], "aceito")
        self.assertTrue(env.center.on_status("wamid.1", "delivered"))
        self.assertEqual(env.last()["status"], "entregue")
        env.center.on_status("wamid.1", "sent")
        self.assertEqual(env.last()["status"], "entregue", "um 'sent' atrasado não volta o estado")
        env.center.on_status("wamid.1", "read")
        self.assertEqual(env.last()["status"], "lido")
        self.assertFalse(env.center.on_status("wamid.nao-existe", "read"))
        self.assertFalse(env.center.on_status("wamid.1", "estranho"))
        env.center.emit("alerta", "s:2", "Teste 2", "x")
        env.center.deliver_pending()
        env.center.on_status("wamid.2", "failed", {"code": 131026, "title": "Undeliverable"})
        self.assertEqual(env.last()["status"], "falhou")
        self.assertIn("131026", env.last()["deliveries"][0]["error"])

    def test_audit_events_become_alerts_and_supervisor_events_are_read_once(self):
        env = Env(self)
        env.audit.add_listener(env.center.on_audit)
        env.audit.record("servico", "encerramento_inesperado", "aviso", actor="sistema", detail="Último sinal de vida: 10:00")
        env.audit.record("site", "site_publicado", "ok", actor="painel", target="x")              # não é alerta
        titles = [e["title"] for e in env.center.list()]
        self.assertEqual(titles, ["O CDBS parou sem desligar normalmente e voltou"])
        env.center.first_run_cursor()
        other = audit.AuditLog(env.root / "system", clock=lambda: env.clock[0] + 5)              # "outro processo"
        other.record("tunel", "tunel_link_novo", "info", actor="supervisor", detail="https://abc-def.trycloudflare.com")
        other.record("servico", "servidor_reiniciado", "falha", actor="supervisor", detail="saiu com código 1")
        self.assertEqual(env.center.scan_supervisor_audit(), 2)
        self.assertEqual(env.center.scan_supervisor_audit(), 0, "o mesmo evento do supervisor não vira dois avisos")
        titles = [e["title"] for e in env.center.list()]
        self.assertIn("Novo link público", titles)
        self.assertIn("O servidor do CDBS caiu e foi reiniciado pelo supervisor", titles)
        link = next(e for e in env.center.list() if e["title"] == "Novo link público")
        self.assertIn("abc-def.trycloudflare.com", link["text"])
        # atualizar e voltar dentro do intervalo de espera: são DUAS trocas de versão, não um alerta repetido
        other.record("atualizacao", "atualizacao_concluida", "ok", actor="supervisor", detail="3.0.0 → 3.0.1", ref="j1")
        other.record("atualizacao", "atualizacao_concluida", "ok", actor="supervisor", detail="3.0.1 → 3.0.0", ref="j2")
        self.assertEqual(env.center.scan_supervisor_audit(), 2)
        updates = [e for e in env.center.list() if e["title"] == "Atualização instalada"]
        self.assertEqual(len(updates), 2)
        self.assertNotIn("agrupado", [e["status"] for e in updates])
        # já a mesma queda repetida dentro da espera é agrupada (sem enxurrada de mensagens)
        other.record("servico", "servidor_reiniciado", "falha", actor="supervisor", detail="saiu com código 1")
        env.center.scan_supervisor_audit()
        crashes = [e for e in env.center.list() if e["title"].startswith("O servidor do CDBS caiu")]
        self.assertEqual(sorted(e["status"] for e in crashes)[0], "agrupado")

    def test_server_app_events_alert_and_recovery_matches_the_same_server(self):
        # 4.0: o guardião grava no histórico (categoria "servidores"); a central lê e avisa
        env = Env(self)
        env.center.first_run_cursor()
        sup = audit.AuditLog(env.root / "system", clock=lambda: env.clock[0] + 5)
        sup.record("servidores", "app_ligado", "ok", actor="supervisor", target="recados")                      # rotina: não avisa
        sup.record("servidores", "app_caiu", "falha", actor="supervisor", target="recados", detail="saiu com código 1; nova tentativa em 2 s")
        self.assertEqual(env.center.scan_supervisor_audit(), 2)
        alerts = [e for e in env.center.list() if e["kind"] == "alerta"]
        self.assertEqual(len(alerts), 1)
        self.assertIn("recados", alerts[0]["text"])
        self.assertEqual(alerts[0]["status"], "pendente")
        env.center.deliver_pending()
        sup.record("servidores", "app_no_ar", "ok", actor="supervisor", target="contador")                      # outro servidor: nada a recuperar
        sup.record("servidores", "app_no_ar", "ok", actor="supervisor", target="recados")
        env.center.scan_supervisor_audit()
        back = {e["key"].split(":")[2]: e["status"] for e in env.center.list() if e["kind"] == "recuperacao"}
        self.assertEqual(back, {"contador": "registrado", "recados": "pendente"}, "só avisa a volta do servidor cuja queda foi avisada")
        sup.record("servidores", "app_parado_por_falhas", "falha", actor="supervisor", target="recados", detail="6 quedas em 15 min")
        env.center.scan_supervisor_audit()
        self.assertTrue(any("deixado parado" in e["title"] for e in env.center.list()))

    def test_worker_thread_delivers_in_background_and_stops(self):
        env = Env(self)
        env.center._poll = 0.05
        env.center.start()
        self.addCleanup(env.center.stop)
        env.center.emit("teste", "t:1", "Teste", "x", force=True)
        deadline = time.time() + 5
        while time.time() < deadline and not env.sender.sent:
            time.sleep(0.05)
        self.assertEqual(len(env.sender.sent), 1)
        env.center.stop()
        self.assertIsNone(env.center._thread)


if __name__ == "__main__":
    unittest.main()
