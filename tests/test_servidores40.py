"""4.0 — servidores (apps) pelo painel e pelo endereço público:
rotas protegidas, criação e colisão com sites, envio do código, repasse /<nome>/ (caminho, consulta, corpo, cabeçalhos,
redirecionamento, cookie, resposta longa, HEAD/PATCH/OPTIONS), estados de erro (desligado, fora do ar, limites, WebSocket),
pedidos ao supervisor (inclusive com o guardião da 3.0 ainda rodando), registro, armazenamento e visão geral."""
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
import apps
import backup
import cdbs
from sysutil import write_json_private
from test_apps import free_base
from test_catalog import request, stack
from test_painel30 import Admin, site_zip

ECO = r'''
import http.server, json, os
class H(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, *a): pass
    def _eco(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else b""
        if self.path.startswith("/redir"):
            self.send_response(302); self.send_header("Location", "/destino?x=1"); self.send_header("Content-Length", "0"); self.end_headers(); return
        if self.path.startswith("/cookie"):
            self.send_response(200); self.send_header("Set-Cookie", "s=1; Path=/; HttpOnly"); self.send_header("Content-Length", "2"); self.end_headers(); self.wfile.write(b"ok"); return
        if self.path.startswith("/longo"):
            self.send_response(200); self.send_header("Transfer-Encoding", "chunked"); self.end_headers()
            for _ in range(48):
                part = b"x" * 65536
                self.wfile.write(b"%x\r\n" % len(part) + part + b"\r\n")
            self.wfile.write(b"0\r\n\r\n"); return
        out = json.dumps({"method": self.command, "path": self.path, "body": body.decode(), "prefix": self.headers.get("X-Forwarded-Prefix"),
                          "host": self.headers.get("Host"), "xff": self.headers.get("X-Forwarded-For"), "hdr": self.headers.get("X-Teste")}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(out))); self.end_headers()
        if self.command != "HEAD": self.wfile.write(out)
    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = do_HEAD = _eco
http.server.ThreadingHTTPServer((os.environ["HOST"], int(os.environ["PORT"])), H).serve_forever()
'''


def zip_bytes(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, content in files.items():
            z.writestr(name, content)
    return buf.getvalue()


class ServidoresTests(unittest.TestCase):
    def setUp(self):
        self.base_port = free_base()
        env = patch.dict(os.environ, {"CDBS_APP_PORT_BASE": str(self.base_port)})
        env.start()
        self.addCleanup(env.stop)
        self.stack = stack()
        self.urls = self.stack.__enter__()
        self.addCleanup(self.stack.__exit__, None, None, None)
        for p in (patch.object(cdbs, "SYSTEM_GATE", backup.RateGate(10_000, 60)),
                  patch.object(cdbs, "SITE_PORT", int(self.urls["public"].rsplit(":", 1)[1]))):
            p.start()
            self.addCleanup(p.stop)
        self.admin = Admin(self.urls)
        self.data = cdbs.DATA
        self.runner = apps.AppRunner(apps.AppStore(self.data), say=lambda _t: None, start_grace=8)
        self.addCleanup(self.runner.stop_all)

    def supervisor(self, features=("apps",)):
        write_json_private(self.data / "system" / "supervisor.json", {"pid": os.getpid(), "heartbeat_at": int(time.time()),
                                                                       "started_at": int(time.time()), "features": list(features), "server": {}})

    def create(self, name="eco", **extra):
        status, data = self.admin.call("POST", "/api/apps", {"name": name, "runtime": "python", "entry": "app.py", **extra})
        self.assertEqual(status, 201, data)
        return data["app"]

    def upload(self, name, files):
        return self.admin.call("POST", f"/api/apps/{name}/upload", body=zip_bytes(files), headers={"Content-Type": "application/zip"})

    def running(self, name="eco", **extra):
        self.create(name, **extra)
        self.assertEqual(self.upload(name, {"app.py": ECO})[0], 201)
        status, msg, _ = self.runner.op_deploy(name, pump=lambda: None)
        self.assertEqual(status, "ok", msg)
        self.runner.write_state()

    def pub(self, path, method="GET", body=None, headers=None):
        return request(self.urls["public"] + path, method, body=body, headers=headers)

    def test_routes_are_protected_like_the_rest_of_the_panel(self):
        self.create("eco")
        for path in ("/api/apps", "/api/storage", "/api/apps/eco/logs"):
            self.assertEqual(self.admin.call("GET", path, cookie=False)[0], 401, path)
            self.assertEqual(self.admin.call("GET", path)[0], 200, path)
        for path, body in (("/api/apps", {"name": "x2", "runtime": "python", "entry": "a.py"}), ("/api/apps/eco/env", {"set": {"A": "1"}}),
                           ("/api/apps/eco/start", {}), ("/api/apps/eco/config", {"health_path": "/"})):
            self.assertEqual(self.admin.call("POST", path, body, cookie=False)[0], 401, path)
            self.assertEqual(self.admin.call("POST", path, body, origin=False)[0], 403, path)
            self.assertEqual(self.admin.call("POST", path, body, marker=False)[0], 403, path)
            self.assertEqual(self.admin.call("POST", path, body=b"a=1", headers={"Content-Type": "text/plain"})[0], 415, path)
        self.assertEqual(self.admin.call("POST", "/api/apps/eco/upload", body=b"PK", headers={"Content-Type": "application/zip"}, marker=False)[0], 403)
        self.assertEqual(self.pub("/api/apps")[0], 404, "nada do painel no endereço público")

    def test_names_are_shared_between_sites_and_servers(self):
        status, _ = self.admin.call("POST", "/api/sites?slug=loja", body=site_zip(), headers={"Content-Type": "application/zip"}, marker=False)
        self.assertEqual(status, 201)
        status, data = self.admin.call("POST", "/api/apps", {"name": "loja", "runtime": "python", "entry": "app.py"})
        self.assertEqual(status, 409)
        self.assertIn("SITE", data["error"])
        self.create("api-loja")
        status, data = self.admin.call("POST", "/api/sites?slug=api-loja", body=site_zip(), headers={"Content-Type": "application/zip"}, marker=False)
        self.assertEqual(status, 409)
        self.assertIn("SERVIDOR", data["error"])

    def test_public_address_forwards_everything_to_the_server(self):
        self.running()
        status, headers, body = self.pub("/eco/a/b?q=1&z=%C3%A7", headers={"X-Teste": "valor"})
        self.assertEqual(status, 200)
        info = json.loads(body)
        self.assertEqual(info["path"], "/a/b?q=1&z=%C3%A7", "prefixo retirado, consulta preservada")
        self.assertEqual(info["prefix"], "/eco")
        self.assertEqual(info["hdr"], "valor")
        self.assertTrue(info["xff"])
        self.assertEqual(headers.get("x-content-type-options"), "nosniff")
        status, _, body = self.pub("/eco/form", "POST", body=b"nome=ana&x=1", headers={"Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(json.loads(body)["body"], "nome=ana&x=1")
        for method in ("PUT", "PATCH", "DELETE", "OPTIONS"):
            status, _, body = self.pub("/eco/r", method, body=b"{}" if method != "OPTIONS" else None)
            self.assertEqual((status, json.loads(body)["method"]), (200, method))
        status, headers, body = self.pub("/eco/r", "HEAD")
        self.assertEqual((status, body), (200, b""))
        status, headers, _ = request(self.urls["public"] + "/eco", "GET")
        self.assertEqual(status, 200, "/eco redireciona para /eco/ (o urllib segue)")
        opener = __import__("urllib.request").request.build_opener(NoRedirect)
        try:
            opener.open(self.urls["public"] + "/eco/redir")
        except __import__("urllib.error").error.HTTPError as err:
            self.assertEqual(err.code, 302)
            self.assertEqual(err.headers["Location"], "/eco/destino?x=1", "redirecionamento volta para dentro de /eco/")
        status, headers, _ = self.pub("/eco/cookie")
        self.assertIn("Path=/eco/", headers["set-cookie"], "cookie fica restrito ao servidor")
        status, _, body = self.pub("/eco/longo")
        self.assertEqual((status, len(body)), (200, 48 * 65536), "resposta longa repassada inteira, em pedaços")
        self.assertEqual(self.pub("/eco/x", "POST", body=b"x" * 10, headers={"Upgrade": "websocket", "Connection": "Upgrade"})[0], 501)
        self.assertEqual(self.pub("/outro/x", "POST", body=b"1")[0], 405, "sites continuam só leitura")

    def test_off_down_and_limits(self):
        self.running()
        self.admin.call("POST", "/api/apps/eco/config", {"max_body_mb": 1})
        self.assertEqual(self.pub("/eco/x", "POST", body=b"x" * (1024 * 1024 + 1))[0], 413)
        self.runner.stop("eco")
        status, _, body = self.pub("/eco/")
        self.assertEqual(status, 502)
        self.assertIn("não respondeu", body.decode())
        self.runner.op_stop("eco")
        status, _, body = self.pub("/eco/")
        self.assertEqual(status, 503)
        self.assertIn("desligado", body.decode())

    def test_actions_go_to_the_supervisor_and_explain_an_old_supervisor(self):
        self.create("eco")
        status, data = self.admin.call("POST", "/api/apps/eco/start", {})
        self.assertEqual(status, 409)
        self.assertIn("bash start.sh", data["error"])
        self.supervisor(features=())                                # guardião da 3.0 ainda rodando depois de atualizar
        status, data = self.admin.call("POST", "/api/apps/eco/start", {})
        self.assertEqual(status, 409)
        self.assertIn("versão anterior", data["error"])
        self.assertIn("versão anterior", self.admin.call("GET", "/api/apps")[1]["note"])
        self.supervisor()
        status, data = self.admin.call("POST", "/api/apps/eco/start", {})
        self.assertEqual(status, 202)
        req = json.loads((self.data / "system" / "control" / "requests" / f"{data['request']}.json").read_text())
        self.assertEqual((req["op"], req["args"]), ("app_start", {"name": "eco"}))
        self.assertEqual(self.admin.call("POST", "/api/apps/eco/stop", {})[0], 409, "parar pede confirmação")
        self.assertEqual(self.admin.call("POST", "/api/apps/eco/remove", {"confirm_text": "outro"})[0], 409)
        self.assertEqual(self.admin.call("POST", "/api/apps/eco/remove", {"confirm_text": "eco"})[0], 202)
        self.assertEqual(self.admin.call("POST", "/api/apps/nao-existe/start", {})[0], 404)

    def test_view_logs_env_storage_and_overview(self):
        self.supervisor()
        self.running()
        self.admin.call("POST", "/api/apps/eco/env", {"set": {"API_KEY": "segredo-xyz"}})
        status, view = self.admin.call("GET", "/api/apps")
        self.assertEqual(status, 200)
        app = view["apps"][0]
        self.assertEqual((app["name"], app["state"], app["port"]), ("eco", "rodando", self.base_port))
        self.assertEqual(app["env_keys"], ["API_KEY"])
        self.assertNotIn("segredo-xyz", json.dumps(view))
        self.assertEqual(view["free_slots"], [2, 3, 4, 5])
        self.assertTrue(app["local_url"].endswith("/eco/"))
        self.assertEqual(self.admin.call("GET", "/api/apps/eco/logs?lines=5")[1]["name"], "eco")
        storage = self.admin.call("GET", "/api/storage")[1]
        self.assertGreater(storage["device"]["total"], 0)
        self.assertIn("servidores", storage["parts"])
        self.assertIn("eco", storage["apps"])
        ov = self.admin.call("GET", "/api/overview")[1]
        self.assertEqual(ov["apps"]["used"], 1)
        self.assertIn("1 de 5 vagas", ov["apps"]["text"])


class NoRedirect(__import__("urllib.request").request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


if __name__ == "__main__":
    unittest.main()
