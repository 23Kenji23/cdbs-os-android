"""3.0 / Parte 1 — servidor: rota de saúde, ping dos sites, histórico de auditoria das ações do painel, início/parada
registrados, encerramento inesperado detectado e mensagens claras quando não consegue iniciar (processo real)."""
import contextlib
import io
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import cdbs
from test_catalog import origin_of, request, stack


def site_zip(title="Ola"):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("index.html", f"<!doctype html><meta charset=utf-8><h1>{title}</h1>")
    return buffer.getvalue()


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class AdminSession:
    def __init__(self, admin):
        self.admin = admin
        headers = {"Origin": admin}
        status, h, _ = request(admin + "/api/setup", "POST", json_body={"password": "senha-do-admin-123"}, headers=headers)
        assert status == 200, status
        self.cookie = h["set-cookie"].split(";", 1)[0]

    def call(self, method, path, json_body=None, body=None, headers=None):
        sent = {"Origin": self.admin, "Cookie": self.cookie, **(headers or {})}
        status, _, data = request(self.admin + path, method, body=body, headers=sent, json_body=json_body)
        try:
            return status, json.loads(data)
        except ValueError:
            return status, data


class HealthAndAuditTests(unittest.TestCase):
    def test_health_is_local_only_and_reveals_nothing(self):
        with stack() as urls:
            status, _, body = request(urls["admin"] + "/api/health")
            self.assertEqual(status, 200)
            data = json.loads(body)
            self.assertEqual(set(data), {"ok", "version", "started_at", "pid"})
            self.assertEqual(data["version"], cdbs.APP_VERSION)
            host = urls["admin"].split("//")[1]
            evil = urllib.request.Request(urls["admin"] + "/api/health", headers={"Host": "evil.example"})
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(evil, timeout=5)
            self.assertEqual(caught.exception.code, 421)
            self.assertEqual(request(urls["public"] + "/api/health")[0], 404, "a rota de saúde não existe no servidor público")
            status, headers, body = request(urls["public"] + "/_cdbs/ping")
            self.assertEqual((status, body), (200, b"ok"))
            self.assertEqual(headers["cache-control"], "no-store")
            self.assertEqual(request(urls["admin"] + "/_cdbs/ping")[0], 404)
            self.assertTrue(host)

    def test_panel_actions_are_audited_without_secrets(self):
        with stack() as urls:
            admin = AdminSession(urls["admin"])
            self.assertEqual(admin.call("POST", "/api/sites?slug=adega", body=site_zip(), headers={"Content-Type": "application/zip"})[0], 201)
            self.assertEqual(admin.call("POST", "/api/sites?slug=adega", body=site_zip(), headers={"Content-Type": "application/zip"})[0], 409)
            status, created = admin.call("POST", "/api/stores", {"slug": "adega", "name": "Adega", "whatsapp": "85 99999-8888"})
            self.assertEqual(status, 201)
            admin.call("POST", "/api/stores/adega/password", {})
            admin.call("DELETE", "/api/stores/adega")
            admin.call("DELETE", "/api/sites/adega")
            request(urls["admin"] + "/api/login", "POST", json_body={"password": "errada-errada-123"}, headers={"Origin": urls["admin"]})
            log = cdbs.get_audit()
            actions = [(e["category"], e["action"], e["result"]) for e in reversed(log.query(limit=50))]
            for expected in [("seguranca", "senha_painel_criada", "ok"), ("site", "site_publicado", "ok"), ("site", "site_publicado", "falha"),
                             ("loja", "loja_criada", "ok"), ("loja", "senha_loja_trocada", "ok"), ("loja", "loja_removida", "ok"),
                             ("site", "site_removido", "ok"), ("seguranca", "login_painel", "recusado")]:
                self.assertIn(expected, actions)
            raw = (cdbs.DATA / "system" / "audit.jsonl").read_text()
            self.assertNotIn(created["password"], raw, "a senha gerada para o cliente nunca vai para o histórico")
            self.assertNotIn("errada-errada-123", raw)
            self.assertNotIn("senha-do-admin-123", raw)
            self.assertNotIn(admin.cookie.split("=", 1)[1], raw)

    def test_backup_completion_reaches_the_audit_log(self):
        with stack() as urls:
            (cdbs.SITES / "loja").mkdir()
            (cdbs.SITES / "loja" / "index.html").write_text("<h1>x</h1>")
            service = cdbs.get_backup()
            rec, started = service.start_backup("loja", plaintext=True)
            self.assertTrue(started)
            deadline = time.time() + 20
            while time.time() < deadline and service.get("loja", rec["id"])["state"] not in ("CONCLUIDO", "FALHOU"):
                time.sleep(0.05)
            self.assertEqual(service.get("loja", rec["id"])["state"], "CONCLUIDO")
            entry = cdbs.get_audit().last("backup", "backup_concluido")
            self.assertIsNotNone(entry)
            self.assertEqual(entry["target"], "loja")
            self.assertEqual(entry["ref"], rec["id"])
            cdbs._backup_observer({"kind": "restauracao", "state": "REVERTIDO", "slug": "loja", "id": "a" * 12, "error_message": "conferência falhou"})
            self.assertEqual(cdbs.get_audit().last("restauracao")["result"], "falha")
            self.assertTrue(urls)


@contextlib.contextmanager
def real_install():
    """Cópia do programa numa pasta temporária, com portas livres: roda o cdbs.py DE VERDADE (processo separado)."""
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder) / "cdbs"
        shutil.copytree(ROOT, root, ignore=shutil.ignore_patterns("data", "tests", "__pycache__", ".env", "docs", "e2e"))
        site, admin = free_port(), free_port()
        (root / ".env").write_text(f"CDBS_SITE_PORT={site}\nCDBS_ADMIN_PORT={admin}\n")
        yield root, site, admin


def start(root, admin, poll=0.1):
    proc = subprocess.Popen([sys.executable, "-u", "cdbs.py", "serve"], cwd=root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{admin}/api/health", timeout=1) as response:
                if response.status == 200:
                    return proc
        except OSError:
            if proc.poll() is not None:
                break
            time.sleep(poll)
    out = proc.stdout.read() if proc.poll() is not None else ""
    proc.kill()
    raise AssertionError(f"o servidor não subiu: {out}")


def audit_actions(root):
    path = root / "data" / "system" / "audit.jsonl"
    return [json.loads(line)["action"] for line in path.read_text().splitlines()] if path.exists() else []


class ProcessLifecycleTests(unittest.TestCase):
    def test_sigterm_right_after_it_answers_is_still_a_clean_stop(self):
        # Pedido de parada logo depois de o servidor responder (reinício/atualização rápidos). Antes da correção, metade
        # das vezes o processo morria sem desligamento limpo e o próximo início acusava "encerramento inesperado".
        with real_install() as (root, site, admin):
            for attempt in range(8):
                with self.subTest(tentativa=attempt):
                    proc = start(root, admin, poll=0.005)                   # pega o servidor no PRIMEIRO instante em que responde
                    proc.send_signal(signal.SIGTERM)
                    proc.communicate(timeout=15)
                    self.assertEqual(proc.returncode, 0)
                    self.assertTrue(json.loads((root / "data" / "system" / "cdbs-heartbeat.json").read_text())["clean"])
            self.assertNotIn("encerramento_inesperado", audit_actions(root))

    def test_clean_stop_unclean_kill_and_detection_on_next_start(self):
        with real_install() as (root, site, admin):
            proc = start(root, admin)
            proc.send_signal(signal.SIGTERM)
            proc.communicate(timeout=15)
            self.assertEqual(proc.returncode, 0)
            beat = json.loads((root / "data" / "system" / "cdbs-heartbeat.json").read_text())
            self.assertTrue(beat["clean"])
            actions = audit_actions(root)
            self.assertEqual(actions[-1], "servico_parado")
            self.assertLess(actions.index("servico_iniciado"), actions.index("servico_parado"))
            proc = start(root, admin)
            proc.kill()                                                   # "o Android matou o Termux"
            proc.communicate(timeout=10)
            self.assertFalse(json.loads((root / "data" / "system" / "cdbs-heartbeat.json").read_text())["clean"])
            proc = start(root, admin)
            try:
                actions = audit_actions(root)
                self.assertIn("encerramento_inesperado", actions)
                last_start = len(actions) - 1 - actions[::-1].index("servico_iniciado")
                self.assertEqual(actions[last_start - 1], "encerramento_inesperado", "o aviso vem logo antes do novo início")
                entry = [json.loads(l) for l in (root / "data" / "system" / "audit.jsonl").read_text().splitlines()
                         if json.loads(l)["action"] == "encerramento_inesperado"][-1]
                self.assertIn("Último sinal de vida", entry["detail"])
            finally:
                proc.send_signal(signal.SIGTERM)
                out, _ = proc.communicate(timeout=15)
            self.assertIn("parou sem desligar normalmente", out)

    def test_busy_port_gives_clear_message_and_exit_code_3(self):
        with real_install() as (root, site, admin):
            with socket.socket() as blocker:
                blocker.bind(("127.0.0.1", site))
                blocker.listen()
                done = subprocess.run([sys.executable, "cdbs.py", "serve"], cwd=root, capture_output=True, text=True, timeout=30)
            self.assertEqual(done.returncode, cdbs.EXIT_CANNOT_START)
            self.assertIn(f"a porta {site} (sites) já está em uso", done.stderr)
            self.assertNotIn("Traceback", done.stderr)
            self.assertIn("inicio_falhou", audit_actions(root))
            usage = subprocess.run([sys.executable, "cdbs.py", "comando-errado"], cwd=root, capture_output=True, text=True, timeout=30)
            self.assertEqual(usage.returncode, 2)
            self.assertIn("Uso:", usage.stderr)


if __name__ == "__main__":
    unittest.main()
