"""3.0 / Partes 2.4 e 3.6 — API do painel operacional: autenticação, origem, cabeçalho, tipo de conteúdo, limite de
pedidos, monitoramento, pausa de site (o público recebe 503), configurações, credenciais só de gravação,
notificações, túnel/servidor sem supervisor, atualizações, histórico com filtros e sessões que sobrevivem a reinício."""
import io
import json
import os
import stat
import sys
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import backup
import cdbs
from test_catalog import request, stack
from test_updater import package_bytes

TOKEN = "EAAGtokenFALSOparaTESTE0123456789abcdefABCDEF"


def site_zip(title="Ola"):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("index.html", f"<!doctype html><meta charset=utf-8><h1>{title}</h1>")
    return buffer.getvalue()


class Admin:
    def __init__(self, urls):
        self.base = urls["admin"]
        status, headers, _ = request(self.base + "/api/setup", "POST", json_body={"password": "senha-do-admin-123"}, headers={"Origin": self.base})
        assert status == 200
        self.cookie = headers["set-cookie"].split(";", 1)[0]

    def call(self, method, path, json_body=None, body=None, headers=None, cookie=True, origin=True, marker=True):
        sent = dict(headers or {})
        if cookie:
            sent["Cookie"] = self.cookie
        if origin:
            sent["Origin"] = self.base
        if marker:
            sent["X-CDBS-Request"] = "1"
        status, _, data = request(self.base + path, method, body=body, headers=sent, json_body=json_body)
        try:
            return status, json.loads(data)
        except ValueError:
            return status, data


class PanelApiTests(unittest.TestCase):
    def setUp(self):
        self.stack = stack()
        self.urls = self.stack.__enter__()
        self.addCleanup(self.stack.__exit__, None, None, None)
        self.gate = patch.object(cdbs, "SYSTEM_GATE", backup.RateGate(10_000, 60))
        self.gate.start()
        self.addCleanup(self.gate.stop)
        self.port = patch.object(cdbs, "SITE_PORT", int(self.urls["public"].rsplit(":", 1)[1]))
        self.port.start()
        self.addCleanup(self.port.stop)
        self.admin = Admin(self.urls)

    def publish(self, slug):
        status, data = self.admin.call("POST", f"/api/sites?slug={slug}", body=site_zip(slug), headers={"Content-Type": "application/zip"}, marker=False)
        self.assertEqual(status, 201, data)
        return data

    def wait_report(self, predicate, timeout=15):
        deadline = time.time() + timeout
        while time.time() < deadline:
            status, view = self.admin.call("GET", "/api/monitor")
            if status == 200 and predicate(view):
                return view
            time.sleep(0.1)
        self.fail(f"o monitor não chegou ao estado esperado: {view}")

    def test_every_new_route_requires_session_origin_marker_and_json(self):
        gets = ["/api/overview", "/api/monitor", "/api/monitor/history", "/api/events", "/api/notifications", "/api/notifications/preview",
                "/api/settings", "/api/updates", "/api/tunnel", "/api/control/0123456789abcdef"]
        for path in gets:
            with self.subTest(path=path):
                self.assertEqual(self.admin.call("GET", path, cookie=False)[0], 401)
                self.assertEqual(self.admin.call("GET", path)[0], 200)
        posts = [("/api/monitor/check", {}), ("/api/monitor/slots", {"slot": 1, "slug": None}), ("/api/sites/x/pause", {"confirm": True}),
                 ("/api/tunnel/start", {}), ("/api/system/restart", {"confirm": True}), ("/api/settings/monitor", {"enabled": True}),
                 ("/api/secrets", {"name": "whatsapp_token", "value": "x"}), ("/api/notifications/test", {"confirm": True})]
        for path, body in posts:
            with self.subTest(path=path):
                self.assertEqual(self.admin.call("POST", path, body, cookie=False)[0], 401)
                self.assertEqual(self.admin.call("POST", path, body, origin=False)[0], 403)
                self.assertEqual(self.admin.call("POST", path, body, headers={"Origin": "http://evil.example"}, origin=False)[0], 403)
                self.assertEqual(self.admin.call("POST", path, body, marker=False)[0], 403)
                self.assertEqual(self.admin.call("POST", path, body=b"x=1", headers={"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.admin.call("DELETE", "/api/secrets/whatsapp_token", marker=False)[0], 403)
        self.assertEqual(self.admin.call("DELETE", "/api/secrets/whatsapp_token", cookie=False)[0], 401)
        for path in ("/api/overview", "/api/settings", "/api/updates"):
            self.assertEqual(request(self.urls["public"] + path)[0], 404, "nada do painel existe no servidor público")

    def test_rate_limit_applies_to_writes(self):
        with patch.object(cdbs, "SYSTEM_GATE", backup.RateGate(2, 60)):
            self.assertEqual(self.admin.call("POST", "/api/monitor/check", {})[0], 202)
            self.admin.call("POST", "/api/monitor/check", {})
            self.assertEqual(self.admin.call("POST", "/api/monitor/check", {})[0], 429)
            self.assertEqual(self.admin.call("GET", "/api/monitor")[0], 200, "leituras não entram no limite")

    def test_publish_assigns_slot_check_pause_resume_and_remove(self):
        self.assertEqual(self.publish("adega")["slot"], 1)
        self.assertEqual(self.publish("loja")["slot"], 2)
        view = self.wait_report(lambda v: [s["state"] for s in v["slots"][:2]] == ["ATIVO", "ATIVO"])
        self.assertEqual(len(view["slots"]), 20)
        self.assertEqual(view["slots"][0]["local_url"], f"http://127.0.0.1:{cdbs.SITE_PORT}/adega/")
        self.assertEqual(self.admin.call("POST", "/api/sites/adega/pause", {})[0], 409, "pausar exige confirmação")
        status, data = self.admin.call("POST", "/api/sites/adega/pause", {"confirm": True, "reason": "cliente não pagou"})
        self.assertEqual(status, 200, data)
        status, headers, body = request(self.urls["public"] + "/adega/")
        self.assertEqual(status, 503)
        self.assertIn("temporariamente fora do ar", body.decode())
        self.wait_report(lambda v: v["slots"][0]["state"] == "PAUSADO" and v["slots"][0]["paused"])
        self.assertEqual(request(self.urls["public"] + "/loja/")[0], 200, "pausar um site não afeta os outros")
        self.assertEqual(self.admin.call("POST", "/api/sites/adega/resume", {})[0], 200)
        self.assertEqual(request(self.urls["public"] + "/adega/")[0], 200)
        self.assertEqual(self.admin.call("POST", "/api/sites/nao-existe/pause", {"confirm": True})[0], 404)
        self.assertEqual(self.admin.call("POST", "/api/monitor/slots", {"slot": 3, "slug": "adega"})[0], 409, "um site não ocupa duas posições")
        self.assertEqual(self.admin.call("POST", "/api/monitor/slots", {"slot": 0, "slug": None})[0], 400)
        self.assertEqual(self.admin.call("POST", "/api/monitor/slots", {"slot": 1, "slug": None})[0], 200)
        self.assertEqual(self.admin.call("POST", "/api/monitor/slots", {"slot": 5, "slug": "adega"})[0], 200)
        self.assertEqual(self.admin.call("DELETE", "/api/sites/adega", marker=False)[0], 200)
        view = self.admin.call("GET", "/api/monitor")[1]
        self.assertEqual(view["slots"][4]["state"], "VAGO", "remover pelo painel libera a posição")
        self.assertEqual(view["slots"][1]["slug"], "loja", "as outras posições não mudam")
        audit_actions = [e["action"] for e in self.admin.call("GET", "/api/events?category=site")[1]["events"]]
        for action in ("site_pausado", "site_retomado", "site_removido", "site_publicado"):
            self.assertIn(action, audit_actions)

    def test_removed_site_is_kept_recoverable_and_only_deleted_on_explicit_request(self):
        self.publish("adega")
        original = (cdbs.SITES / "adega" / "index.html").read_bytes()
        status, data = self.admin.call("DELETE", "/api/sites/adega", marker=False)
        self.assertEqual((status, data["kept"]), (200, True))
        self.assertEqual(request(self.urls["public"] + "/adega/")[0], 404, "removido sai do ar na hora")
        self.assertFalse((cdbs.SITES / "adega").exists())
        status, listing = self.admin.call("GET", "/api/removed-sites")
        self.assertEqual(status, 200)
        item = listing["items"][0]
        self.assertEqual((item["slug"], item["files"], item["published_now"]), ("adega", 1, False))
        kept = cdbs.DATA / "removidos" / "sites" / item["id"]
        self.assertEqual(stat.S_IMODE(kept.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((cdbs.DATA / "removidos").stat().st_mode), 0o700)
        for path in (f"/{item['id']}/", "/removidos/", "/_removidos/"):
            self.assertEqual(request(self.urls["public"] + path)[0], 404, "a cópia guardada nunca é servida")
        # publicar de novo com o mesmo nome e tentar recuperar a antiga: conflito explicado, nada é sobrescrito
        self.publish("adega")
        self.assertTrue(self.admin.call("GET", "/api/removed-sites")[1]["items"][0]["published_now"])
        status, data = self.admin.call("POST", f"/api/removed-sites/{item['id']}/restore", {})
        self.assertEqual(status, 409, data)
        self.assertIn("Remova o atual primeiro", data["error"])
        status, data = self.admin.call("DELETE", "/api/sites/adega", marker=False)                  # a nova também fica guardada
        self.assertEqual(len(self.admin.call("GET", "/api/removed-sites")[1]["items"]), 2)
        status, data = self.admin.call("POST", f"/api/removed-sites/{item['id']}/restore", {})
        self.assertEqual((status, data["slug"]), (200, "adega"), data)
        self.assertEqual((cdbs.SITES / "adega" / "index.html").read_bytes(), original, "voltou exatamente o que tinha sido removido")
        self.assertEqual(request(self.urls["public"] + "/adega/")[0], 200)
        self.assertIsNotNone(data["slot"], "volta para uma posição do monitoramento")
        # apagar de vez: só com APAGAR digitado; id inválido ou inexistente não acha nada
        other = self.admin.call("GET", "/api/removed-sites")[1]["items"][0]["id"]
        self.assertEqual(self.admin.call("DELETE", f"/api/removed-sites/{other}", {})[0], 409)
        self.assertEqual(self.admin.call("DELETE", f"/api/removed-sites/{other}", {"confirm_text": "APAGAR"}, marker=False)[0], 403)
        self.assertEqual(self.admin.call("DELETE", "/api/removed-sites/000000000000", {"confirm_text": "APAGAR"})[0], 404)
        self.assertEqual(self.admin.call("DELETE", "/api/removed-sites/..%2f..%2fsites", {"confirm_text": "APAGAR"})[0], 404)
        self.assertEqual(self.admin.call("POST", "/api/removed-sites/nao-e-id/restore", {})[0], 404)
        self.assertEqual(self.admin.call("DELETE", f"/api/removed-sites/{other}", {"confirm_text": "APAGAR"})[0], 200)
        self.assertEqual(self.admin.call("GET", "/api/removed-sites")[1]["items"], [])
        self.assertTrue((cdbs.SITES / "adega" / "index.html").is_file(), "apagar a cópia não mexe no site publicado")
        actions = [e["action"] for e in self.admin.call("GET", "/api/events?category=site")[1]["events"]]
        for action in ("site_removido", "site_recuperado", "site_apagado"):
            self.assertIn(action, actions)

    def test_settings_validation_env_lock_and_write_only_secrets(self):
        self.assertEqual(self.admin.call("POST", "/api/settings/monitor", {"interval_minutes": 2})[0], 400)
        self.assertEqual(self.admin.call("POST", "/api/settings/naoexiste", {"a": 1})[0], 400)
        status, data = self.admin.call("POST", "/api/settings/monitor", {"interval_minutes": 45})
        self.assertEqual((status, data["section"]["interval_minutes"]), (200, 45))
        with patch.dict(os.environ, {"CDBS_MONITOR_PUBLIC_CHECK": "true"}):
            self.assertEqual(self.admin.call("POST", "/api/settings/monitor", {"public_check": False})[0], 409)
            self.assertIn("monitor.public_check", self.admin.call("GET", "/api/settings")[1]["locked_by_env"])
        status, data = self.admin.call("POST", "/api/secrets", {"name": "whatsapp_token", "value": TOKEN})
        self.assertEqual(status, 200)
        self.assertTrue(data["secrets"]["whatsapp_token"]["configured"])
        everything = json.dumps(self.admin.call("GET", "/api/settings")[1]) + json.dumps(self.admin.call("GET", "/api/events")[1])
        self.assertNotIn(TOKEN, everything)
        self.assertEqual(stat.S_IMODE((cdbs.DATA / "system" / "secrets.json").stat().st_mode), 0o600)
        self.assertNotIn(TOKEN, (cdbs.DATA / "system" / "audit.jsonl").read_text())
        self.assertEqual(self.admin.call("POST", "/api/secrets", {"name": "../x", "value": "y"})[0], 400)
        self.assertEqual(self.admin.call("POST", "/api/secrets", {"name": "whatsapp_token", "value": "com espaço"})[0], 400)
        status, data = self.admin.call("POST", "/api/secrets/verify-token", {})
        self.assertEqual(status, 200)
        self.assertNotIn(data["verify_token"], json.dumps(self.admin.call("GET", "/api/settings")[1]), "o token de verificação aparece uma vez só")
        self.assertEqual(self.admin.call("DELETE", "/api/secrets/whatsapp_token")[0], 200)
        self.assertFalse(self.admin.call("GET", "/api/settings")[1]["secrets"]["whatsapp_token"]["configured"])

    def test_notifications_tunnel_and_restart_without_supervisor(self):
        status, data = self.admin.call("GET", "/api/notifications")
        self.assertEqual(data["status"]["state"], "desativado")
        self.assertEqual(self.admin.call("POST", "/api/notifications/test", {})[0], 409)
        status, data = self.admin.call("POST", "/api/notifications/test", {"confirm": True})
        self.assertEqual((status, data["event"]["status"]), (202, "desativado"))
        self.assertIn("só no histórico", data["event"]["note"])
        self.assertEqual(self.admin.call("POST", "/api/tunnel/stop", {})[0], 409)
        status, data = self.admin.call("POST", "/api/tunnel/start", {})
        self.assertEqual(status, 409)
        self.assertIn("supervisor não está rodando", data["error"])
        self.assertEqual(self.admin.call("POST", "/api/system/restart", {})[0], 409)
        self.assertEqual(self.admin.call("POST", "/api/system/restart", {"confirm": True})[0], 409)
        tunnel = self.admin.call("GET", "/api/tunnel")[1]
        self.assertFalse(tunnel["supervisor"])
        self.assertEqual(self.admin.call("GET", "/api/control/0123456789abcdef")[1]["status"], "aguardando")

    def test_update_upload_validation_and_confirmation(self):
        status, data = self.admin.call("POST", "/api/updates/upload", body=b"PK", headers={"Content-Type": "text/plain"})
        self.assertEqual(status, 415)
        status, data = self.admin.call("POST", "/api/updates/upload", body=b"nao sou zip", headers={"Content-Type": "application/zip"})
        self.assertEqual(status, 400)
        self.assertIn("não é um ZIP", data["error"])
        status, data = self.admin.call("POST", "/api/updates/upload", body=package_bytes(version="9.0.0"), headers={"Content-Type": "application/zip"})
        self.assertEqual(status, 201, data)
        report = data["package"]
        self.assertTrue(report["ok"], report)
        listing = self.admin.call("GET", "/api/updates")[1]
        self.assertEqual(listing["staged"][0]["id"], report["id"])
        self.assertFalse(listing["supervisor"])
        self.assertEqual(self.admin.call("POST", f"/api/updates/{report['id']}/install", {"confirm_text": "sim"})[0], 409)
        status, data = self.admin.call("POST", f"/api/updates/{report['id']}/install", {"confirm_text": "INSTALAR"})
        self.assertEqual(status, 409)
        self.assertIn("supervisor", data["error"])
        self.assertEqual(self.admin.call("POST", "/api/updates/rollback", {"snapshot": "x", "confirm_text": "VOLTAR"})[0], 404)
        self.assertEqual(self.admin.call("DELETE", f"/api/updates/{report['id']}")[0], 200)
        self.assertEqual(self.admin.call("GET", "/api/updates")[1]["staged"], [])
        events = self.admin.call("GET", "/api/events?category=atualizacao")[1]["events"]
        self.assertEqual(events[0]["action"], "pacote_recebido")

    def test_events_filters_and_invalid_parameters(self):
        self.publish("adega")
        self.assertEqual(self.admin.call("GET", "/api/events?category=inexistente")[0], 400)
        self.assertEqual(self.admin.call("GET", "/api/events?result=talvez")[0], 400)
        self.assertEqual(self.admin.call("GET", "/api/events?limit=abc")[0], 400)
        data = self.admin.call("GET", "/api/events?category=site&result=ok&q=ADEGA&limit=5")[1]
        self.assertTrue(data["events"])
        self.assertTrue(all(e["category"] == "site" and e["result"] == "ok" for e in data["events"]))
        self.assertIn("seguranca", data["categories"])

    def test_admin_session_survives_a_server_restart_and_logout_ends_it(self):
        token = self.admin.cookie.split("=", 1)[1]
        stored = json.loads((cdbs.DATA / "admin-sessions.json").read_text())
        self.assertNotIn(token, json.dumps(stored), "no disco fica só o hash do token")
        self.assertEqual(stat.S_IMODE((cdbs.DATA / "admin-sessions.json").stat().st_mode), 0o600)
        cdbs.SESSIONS.clear()                                                    # "o servidor reiniciou"
        self.assertEqual(self.admin.call("GET", "/api/overview")[0], 200)
        self.assertEqual(self.admin.call("POST", "/api/logout", {}, marker=False)[0], 200)
        cdbs.SESSIONS.clear()
        self.assertEqual(self.admin.call("GET", "/api/overview")[0], 401)

    def test_overview_shape(self):
        self.publish("adega")
        data = self.admin.call("GET", "/api/overview")[1]
        for key in ("version", "started_at", "supervisor", "tunnel", "slots", "last_backup", "notifications", "update", "recent_problems"):
            self.assertIn(key, data)
        self.assertEqual(len(data["slots"]["slots"]), 20)
        self.assertFalse(data["supervisor"]["running"])
        self.assertIn("supervisor não está rodando", data["tunnel"]["message"])


if __name__ == "__main__":
    unittest.main()
