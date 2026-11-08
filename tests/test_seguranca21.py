"""Evolução 2.1 — Correções E e G: lembrete mensal confiável e nova auditoria das rotas (autenticação, origem, CSRF, limites,
caminhos, segredos, permissões, erros seguros). Sem rede."""
import contextlib
import http.client
import io
import json
import os
import stat
import sys
import threading
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import backup
import backup_crypto as bc
import cdbs
import test_backup as tb
from backup import BackupError, BackupService
from test_backup import DAY, FAST_KDF, INDEX, PW, env
from test_catalog import Client, request


# ---------------------------------------------------------------- E: lembrete mensal
class ReminderTests(unittest.TestCase):
    def test_stores_never_opened_in_the_panel_start_counting_at_the_first_check(self):
        with env() as e:
            e.site("loja-a")
            e.site("loja-b")
            status = e.svc.reminder_tick("inicio")
            self.assertEqual((status["stores"], status["overdue"], status["source"]), (2, 0, "inicio"))
            e.clock.t += 31 * DAY
            status = e.svc.reminder_tick("periodico")
            self.assertEqual(status["overdue"], 2)                                      # contou desde a 1ª verificação, sem ninguém abrir o painel
            overview = e.svc.overview()
            self.assertEqual(overview["overdue"], 2)
            self.assertEqual(overview["reminder_status"]["last_run_at"], e.clock.now())
            for row in overview["stores"]:
                self.assertTrue(row["reminder"]["overdue"])
                self.assertEqual(row["reminder"]["last_processed_at"], e.clock.now())     # "última vez que o aviso foi processado"
                self.assertIsNone(row["reminder"]["last_validated_at"])

    def test_last_validated_date_is_recorded_and_resets_the_cycle(self):
        with env() as e:
            e.site()
            e.svc.reminder_tick()                                                        # o CDBS passa a conhecer a loja
            e.clock.t += 40 * DAY
            e.svc.reminder_tick()
            self.assertTrue(e.svc.reminder("loja-a")["overdue"])
            rec = e.backup(plain=False)
            reminder = e.svc.reminder("loja-a")
            self.assertFalse(reminder["overdue"])
            self.assertEqual(reminder["last_validated_at"], rec["finished_at"])
            e.clock.t += 31 * DAY
            self.assertTrue(e.svc.reminder("loja-a")["overdue"])

    def test_no_duplicate_alerts(self):
        with env() as e:
            e.site()
            e.svc.reminder_tick()
            e.clock.t += 45 * DAY
            for _ in range(6):
                e.svc.reminder_tick()
                e.svc.overview()
                e.svc.reminder("loja-a")
            alerts = [ev for ev in e.svc.events(limit=500) if ev["kind"] == "lembrete"]
            self.assertEqual(len(alerts), 1)                                             # várias aberturas/verificações no mesmo dia = 1 aviso
            self.assertEqual(e.svc.reminder("loja-a")["shown_count"], 1)
            restarted = BackupService(e.root, e.sites, e.cats, clock=e.clock.now, disk_free=lambda: 10 ** 12, kdf=FAST_KDF)   # reiniciou o CDBS
            restarted.reminder_tick("inicio")
            self.assertEqual(len([ev for ev in restarted.events(limit=500) if ev["kind"] == "lembrete"]), 1)
            e.clock.t += DAY + 1
            e.svc.reminder_tick()
            self.assertEqual(len([ev for ev in e.svc.events(limit=500) if ev["kind"] == "lembrete"]), 2)   # no dia seguinte, um novo

    def test_overdue_reminder_never_blocks_publishing_or_suspends_a_store(self):
        with env() as e:
            e.site()
            e.store()
            e.svc.reminder_tick()
            before = e.snapshot()
            store_before = (e.cats / "loja-a" / "store.json").read_bytes()
            e.clock.t += 400 * DAY
            e.svc.reminder_tick()
            self.assertTrue(e.svc.reminder("loja-a")["overdue"])
            self.assertEqual(e.snapshot(), before)                                       # não mexe em site nem catálogo
            self.assertEqual((e.cats / "loja-a" / "store.json").read_bytes(), store_before)
            self.assertFalse(e.catalog.is_pending("loja-a"))
            self.assertEqual(e.backup(plain=False)["state"], "CONCLUIDO")                # e continua dando para fazer backup/publicar

    def test_after_android_kills_the_process_the_next_start_catches_up_from_timestamps(self):
        with env() as e:
            e.site()
            e.backup(plain=False)
            e.svc.reminder_tick("inicio")
            self.assertEqual(e.svc.reminder_status()["overdue"], 0)
            e.clock.t += 75 * DAY                                                        # o Termux ficou morto 75 dias: nada rodou
            fresh = BackupService(e.root, e.sites, e.cats, clock=e.clock.now, disk_free=lambda: 10 ** 12, kdf=FAST_KDF)
            self.assertEqual(fresh.reminder_status()["overdue"], 0)                      # sem inventar: o estado antigo continua até rodar de novo
            status = fresh.reminder_tick("inicio")
            self.assertEqual(status["overdue"], 1)
            self.assertEqual(fresh.reminder("loja-a")["days_since"], 75)                 # nada depende de contador em memória

    def test_status_text_does_not_promise_notifications(self):
        with env() as e:
            note = e.svc.reminder_tick()["note"]
            self.assertIn("Não envia notificação do Android", note)
            self.assertIn("celular desligado", note)
            self.assertIn("Termux encerrado", note)
            self.assertIn("a cada 6 horas", note)
            self.assertIn("a cada 6 horas", e.svc.reminder_status()["note"])

    def test_background_thread_runs_at_start_and_periodically_and_survives_errors(self):
        with tb.tempfile.TemporaryDirectory() as folder, contextlib.ExitStack() as stop:
            root = Path(folder)
            (root / "sites" / "loja-a").mkdir(parents=True)
            (root / "sites" / "loja-a" / "index.html").write_bytes(INDEX)
            stop.enter_context(patch.object(cdbs, "DATA", root))
            stop.enter_context(patch.object(cdbs, "SITES", root / "sites"))
            service = cdbs.get_backup()
            calls = []
            real = service.reminder_tick

            def flaky(source="painel"):
                calls.append(source)
                if len(calls) == 2:
                    raise RuntimeError("falha simulada")
                return real(source)
            service.reminder_tick = flaky
            thread = cdbs.start_reminder_thread(0.05)
            self.addCleanup(thread.stop_event.set)
            deadline = time.time() + 5
            while len(calls) < 4 and time.time() < deadline:
                time.sleep(0.02)
            thread.stop_event.set()
            thread.join(2)
            self.assertFalse(thread.is_alive())
            self.assertGreaterEqual(len(calls), 4)                                       # o erro da 2ª rodada não matou a thread
            self.assertEqual(calls[0], "inicio")
            self.assertTrue(all(c == "periodico" for c in calls[1:]))
            self.assertTrue(thread.daemon)
            self.assertEqual(service.reminder_status()["source"], "periodico")


# ---------------------------------------------------------------- G: rotas
class RouteAuditTests(tb.HttpTests):
    """Reaproveita o servidor de teste da Central (admin + sites) e ataca as rotas."""

    def host_request(self, urls, path, method="GET", host=None, headers=None, body=None):
        netloc = urls["admin"].split("//")[1]
        connection = http.client.HTTPConnection(netloc.split(":")[0], int(netloc.split(":")[1]), timeout=10)
        connection.putrequest(method, path, skip_host=True)
        connection.putheader("Host", host or netloc)
        for key, value in (headers or {}).items():
            connection.putheader(key, value)
        if body is not None:
            connection.putheader("Content-Length", str(len(body)))
        connection.endheaders(body)
        response = connection.getresponse()
        data = response.read()
        connection.close()
        return response.status, data

    def make(self, call):
        _, created = call("POST", "/api/backups/loja-a/create", {"password": PW})
        rid = created["backup"]["id"]
        self.wait(call, f"/api/backups/loja-a/{rid}", {"CONCLUIDO"})
        return rid

    ROUTES = (("GET", "/api/backups/overview"), ("GET", "/api/backups/_protecao"), ("POST", "/api/backups/_protecao/setup"),
              ("GET", "/api/backups/_retencao"), ("POST", "/api/backups/_retencao"), ("POST", "/api/backups/_retencao/preview"),
              ("POST", "/api/backups/_retencao/apply"), ("GET", "/api/backups/loja-a"), ("POST", "/api/backups/loja-a/create"),
              ("POST", "/api/backups/loja-a/import"))
    ID_ROUTES = (("GET", "{}"), ("GET", "{}/download"), ("POST", "{}/validate"), ("POST", "{}/protect"), ("POST", "{}/restore/summary"),
                 ("POST", "{}/restore"), ("POST", "{}/delete/summary"), ("POST", "{}/delete"))

    def all_routes(self, rid):
        return list(self.ROUTES) + [(m, f"/api/backups/loja-a/{p.format(rid)}") for m, p in self.ID_ROUTES]

    def test_every_route_requires_a_session(self):
        with self.admin() as (urls, call, raw_get, headers):
            rid = self.make(call)
            admin = urls["admin"]
            anon = {"Origin": admin, "X-CDBS-Request": "1"}
            for method, path in self.all_routes(rid):
                with self.subTest(path=path):
                    status, _, _ = request(admin + path, method, json_body={} if method == "POST" else None, headers=anon)
                    self.assertEqual(status, 401)
            fake = {**anon, "Cookie": "cdbs_session=" + "a" * 43}
            for method, path in self.all_routes(rid)[:6]:
                status, _, _ = request(admin + path, method, json_body={} if method == "POST" else None, headers=fake)
                self.assertEqual(status, 401)

    def test_client_panel_session_is_useless_on_the_admin_server(self):
        with self.admin() as (urls, call, raw_get, headers):
            rid = self.make(call)
            password = cdbs.get_catalog().create_store("loja-b", "Loja B", "")
            client = Client(urls["client"], "loja-b", password)
            self.assertEqual(client.login_status, 200)
            admin = urls["admin"]
            for method, path in self.all_routes(rid):
                status, _, _ = request(admin + path, method, json_body={} if method == "POST" else None,
                                       headers={"Origin": admin, "X-CDBS-Request": "1", "Cookie": client.cookie})
                self.assertEqual(status, 401, path)
            for path in ("/api/backups/overview", f"/_painel/api/backups/loja-a/{rid}/delete"):             # e o painel do cliente não tem rota de backup
                status, _ = client.call("GET", path)
                self.assertIn(status, (401, 404))
            status, _ = client.call("POST", f"/api/backups/loja-a/{rid}/delete", {})
            self.assertIn(status, (401, 403, 404, 405))

    def test_csrf_defences_origin_custom_header_and_host(self):
        with self.admin() as (urls, call, raw_get, headers):
            rid = self.make(call)
            admin = urls["admin"]
            cookie = {"Cookie": headers["Cookie"]}
            body = json.dumps({"password": PW}).encode()
            base = {"Content-Type": "application/json", **cookie}
            netloc = admin.split("//")[1]
            cases = (
                ("sem Origin nem cabeçalho", base, 403), ("só Origin", {**base, "Origin": admin}, 403),
                ("só cabeçalho", {**base, "X-CDBS-Request": "1"}, 403), ("Origin de outro site", {**base, "Origin": "http://evil.example", "X-CDBS-Request": "1"}, 403),
                ("Origin null (formulário de sandbox)", {**base, "Origin": "null", "X-CDBS-Request": "1"}, 403),
                ("Origin com porta errada", {**base, "Origin": "http://127.0.0.1:1", "X-CDBS-Request": "1"}, 403),
                ("cabeçalho com valor errado", {**base, "Origin": admin, "X-CDBS-Request": "0"}, 403))
            for label, hdrs, expected in cases:
                with self.subTest(label):
                    status, _ = self.host_request(urls, f"/api/backups/loja-a/{rid}/delete/summary", "POST", headers=hdrs, body=body)
                    self.assertEqual(status, expected)
            # formulário HTML clássico (urlencoded / multipart / text/plain) não passa mesmo com Origin certo, porque falta o cabeçalho próprio
            for ctype in ("application/x-www-form-urlencoded", "multipart/form-data; boundary=x", "text/plain"):
                status, _ = self.host_request(urls, f"/api/backups/loja-a/{rid}/delete/summary", "POST",
                                              headers={"Content-Type": ctype, "Origin": admin, **cookie}, body=body)
                self.assertEqual(status, 403, ctype)
            # com o cabeçalho certo mas o tipo de conteúdo errado
            status, data = self.host_request(urls, f"/api/backups/loja-a/{rid}/delete/summary", "POST",
                                             headers={"Content-Type": "text/plain", "Origin": admin, "X-CDBS-Request": "1", **cookie}, body=body)
            self.assertEqual(status, 415)
            # tudo certo
            status, _ = self.host_request(urls, f"/api/backups/loja-a/{rid}/delete/summary", "POST",
                                          headers={**base, "Origin": admin, "X-CDBS-Request": "1"}, body=body)
            self.assertEqual(status, 200)
            # DNS rebinding: Host diferente do endereço local é recusado em QUALQUER rota (até GET)
            for host in ("evil.example", f"evil.example:{netloc.split(':')[1]}", "127.0.0.1", "10.0.0.5:" + netloc.split(":")[1], ""):
                with self.subTest(host=host):
                    status, _ = self.host_request(urls, "/api/backups/overview", "GET", host=host or "x", headers={"Origin": admin, **cookie})
                    self.assertEqual(status, 421)
            status, _ = self.host_request(urls, "/api/backups/overview", "GET", host="localhost:" + netloc.split(":")[1], headers=cookie)
            self.assertEqual(status, 200)                                                 # localhost continua valendo
            # a sessão é SameSite=Strict e HttpOnly (outro site não a envia)
            setup_cookie = request(admin + "/api/login", "POST", json_body={"password": "senha-do-admin-123"}, headers={"Origin": admin})[1]["set-cookie"]
            self.assertIn("SameSite=Strict", setup_cookie)
            self.assertIn("HttpOnly", setup_cookie)

    def test_no_cors_is_granted_to_anyone(self):
        with self.admin() as (urls, call, raw_get, headers):
            admin = urls["admin"]
            status, out, _ = request(admin + "/api/backups/overview", "OPTIONS", headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "POST",
                                                                                          "Access-Control-Request-Headers": "x-cdbs-request"})
            self.assertNotIn("access-control-allow-origin", out)
            status, out, _ = request(admin + "/api/backups/overview", "GET", headers={"Origin": "http://evil.example", "Cookie": headers["Cookie"]})
            self.assertNotIn("access-control-allow-origin", out)
            self.assertEqual(out["cache-control"], "no-store")

    def test_parameter_validation_and_path_traversal(self):
        with self.admin() as (urls, call, raw_get, headers):
            rid = self.make(call)
            for slug in ("..", "%2e%2e", "a/b", "A", "loja_a", "x" * 80, "-", "loja-a%00", "loja%20a", "%2e%2e%2f%2e%2e", "con\\fig"):
                for tail in ("", "/create", f"/{rid}", f"/{rid}/delete/summary"):
                    method = "POST" if tail else "GET"
                    status, data = call(method, f"/api/backups/{slug}{tail}", {} if method == "POST" else None)
                    self.assertIn(status, (400, 404), (slug, tail, status))
                    self.assertNotIn("Traceback", json.dumps(data) if isinstance(data, dict) else str(data))
            for bad in ("zzzzzzzzzzzz", rid.upper(), rid[:-1], rid + "0", "../" + rid, "%2e%2e"):
                for method, tail in (("GET", ""), ("GET", "/download"), ("POST", "/validate"), ("POST", "/delete/summary"), ("POST", "/restore/summary")):
                    status, _ = call(method, f"/api/backups/loja-a/{bad}{tail}", {} if method == "POST" else None)
                    self.assertIn(status, (400, 404, 405), (bad, tail, status))
            for body in ({"password": ["lista"]}, {"password": {"a": 1}}, {"password": 12}, {"password": None}, {"password": "x" * 20000}):
                status, _ = call("POST", "/api/backups/loja-a/create", body)
                self.assertIn(status, (403, 413, 400), body.get("password") if len(str(body)) < 60 else "grande")
            for payload in (b"[1,2]", b"\"texto\"", b"nao-e-json", b"{", b"\xff\xfe"):
                status, _ = call("POST", "/api/backups/loja-a/create", extra={"Content-Type": "application/json"}, raw=payload)
                self.assertIn(status, (400, 413), payload)
            status, _ = call("POST", "/api/backups/loja-a/create", extra={"Content-Type": "application/json"}, raw=b"{" + b" " * 20000 + b"}")
            self.assertEqual(status, 413)                                                   # corpo JSON acima do limite
            for value in ("-5", "abc", "99999999999999999999"):
                status, _ = call("POST", "/api/backups/loja-a/create", extra={"Content-Length": value, "Content-Type": "application/json"}, raw=b"{}")
                self.assertNotEqual(status, 500)
            self.assertEqual(len(call("GET", "/api/backups/loja-a")[1]["backups"]), 1)      # nenhuma tentativa criou backup

    def test_password_attempts_are_locked_out_over_http_with_a_generic_message(self):
        with self.admin() as (urls, call, raw_get, headers):
            rid = self.make(call)
            codes = []
            for _ in range(7):
                status, data = call("POST", f"/api/backups/loja-a/{rid}/validate", {"password": "senha-errada-qualquer-1"})
                codes.append((status, data.get("code")))
            self.assertEqual(codes[:5], [(403, "SENHA_INCORRETA")] * 5)
            self.assertEqual(codes[5:], [(429, "SENHA_BLOQUEADA")] * 2)
            status, data = call("POST", f"/api/backups/loja-a/{rid}/validate", {"password": PW})
            self.assertEqual((status, data["code"]), (429, "SENHA_BLOQUEADA"))                # nem a certa passa durante o bloqueio
            blob = json.dumps(data)
            self.assertNotIn("scrypt", blob)
            self.assertNotIn(str(cdbs.DATA), blob)

    def test_errors_are_safe_and_nothing_internal_leaks(self):
        with self.admin() as (urls, call, raw_get, headers):
            rid = self.make(call)
            service = cdbs.get_backup()
            with patch.object(BackupService, "overview", side_effect=RuntimeError(f"/segredo/{cdbs.DATA}/chave")):
                status, data = call("GET", "/api/backups/overview")
            self.assertEqual(status, 500)
            self.assertNotIn("segredo", json.dumps(data))
            self.assertNotIn(str(cdbs.DATA), json.dumps(data))
            for method, path, body in (("GET", f"/api/backups/loja-a/{'0' * 12}", None), ("POST", f"/api/backups/loja-a/{'0' * 12}/restore/summary", {}),
                                       ("GET", f"/api/backups/loja-a/{'0' * 12}/download", None)):
                status, data = call(method, path, body)
                self.assertEqual(status, 404)
                self.assertNotIn(str(cdbs.DATA), json.dumps(data) if isinstance(data, dict) else str(data))
            # nada do conteúdo sensível aparece nas respostas de listagem
            blob = json.dumps([call("GET", "/api/backups/overview")[1], call("GET", "/api/backups/loja-a")[1], call("GET", "/api/backups/loja-a/" + rid)[1],
                               call("GET", "/api/backups/_protecao")[1], call("GET", "/api/backups/_retencao")[1]])
            for secret in (PW, str(cdbs.DATA), "salt", '"check"', "senha-do-admin-123"):
                self.assertNotIn(secret, blob)

    def test_nothing_is_logged_with_passwords_or_urls(self):
        out, err = io.StringIO(), io.StringIO()
        with self.admin() as (urls, call, raw_get, headers), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rid = self.make(call)
            call("POST", f"/api/backups/loja-a/{rid}/validate", {"password": "senha-errada-qualquer-1"})
            call("POST", f"/api/backups/loja-a/{rid}/restore/summary", {})
            call("GET", "/api/backups/segredo-na-url")
        text = out.getvalue() + err.getvalue()
        for secret in (PW, "senha-errada-qualquer-1", "segredo-na-url", "senha-do-admin-123"):
            self.assertNotIn(secret, text)

    def test_import_limits_and_bombs_are_rejected_without_leftovers(self):
        with env() as e:
            e.site()
            small = BackupService(e.root, e.sites, e.cats, max_bytes=20_000, clock=e.clock.now, disk_free=lambda: 10 ** 12, kdf=FAST_KDF)
            bomb_zip = e.root / "bomba.zip"
            with zipfile.ZipFile(bomb_zip, "w", zipfile.ZIP_DEFLATED) as z:
                z.writestr("site/index.html", b"0" * 5_000_000)                           # cabe em poucos KB, abre em 5 MB
            bomb = e.root / "bomba.cdbk"
            bc.encrypt_file(bomb_zip, bomb, PW, FAST_KDF)
            for payload in (bomb.read_bytes(), bomb_zip.read_bytes(), b"x" * 50_000):
                before = sorted(p.name for p in (e.root / "backups" / "loja-a").iterdir()) if (e.root / "backups" / "loja-a").exists() else []
                with self.assertRaises(BackupError):
                    small.import_archive("loja-a", io.BytesIO(payload), len(payload), PW)
                after = [p.name for p in (e.root / "backups" / "loja-a").iterdir() if p.suffix in (".cdbk", ".zip") or p.name.endswith(".part")]
                self.assertEqual(after, [])
            with self.assertRaises(BackupError):                                           # tamanho declarado diferente do real
                real = e.backup(plain=False)
                data = (e.root / "backups" / "loja-a" / f"{real['id']}.cdbk").read_bytes()
                e.svc.import_archive("loja-a", io.BytesIO(data[:-5]), len(data), PW)
            tmp = e.root / "restore-tmp"
            self.assertFalse(tmp.exists() and any(tmp.iterdir()))

    def test_zip_slip_inside_an_encrypted_backup_is_rejected_on_import_and_on_restore(self):
        with env() as e:
            e.site()
            rec = e.backup(plain=True)
            good = tb.zip_entries(e.root / "backups" / "loja-a" / f"{rec['id']}.zip")
            manifest = json.loads(good["manifest.json"])
            for evil in ("site/../../escapou.txt", "/etc/escapou", "site/..\\x", "site//a", "site/./a"):
                forged = dict(good)
                forged["manifest.json"] = json.dumps({**manifest, "files": manifest["files"] + [{"path": evil, "size": 1, "sha256": "0" * 64}]}).encode()
                src = e.root / "forjado.zip"
                with zipfile.ZipFile(src, "w") as z:
                    for name, data in forged.items():
                        z.writestr(name, data)
                    z.writestr(evil, b"x")
                enc = e.root / "forjado.cdbk"
                enc.unlink(missing_ok=True)
                bc.encrypt_file(src, enc, PW, FAST_KDF)
                payload = enc.read_bytes()
                with self.subTest(evil=evil), self.assertRaises(BackupError):
                    e.svc.import_archive("loja-a", io.BytesIO(payload), len(payload), PW)
            self.assertFalse((e.root.parent / "escapou.txt").exists())
            self.assertFalse((e.sites / "escapou.txt").exists())

    def test_permissions_of_everything_the_backup_center_writes(self):
        with self.admin() as (urls, call, raw_get, headers):
            rid = self.make(call)
            call("POST", "/api/backups/_retencao", {"mode": "keep_last", "keep_last": 3})
            cdbs.get_backup().reminder_tick()
            root = cdbs.DATA / "backups"
            for path in root.rglob("*"):
                mode = stat.S_IMODE(path.stat().st_mode)
                if path.is_dir():
                    self.assertEqual(mode, 0o700, str(path.relative_to(root)))
                else:
                    self.assertEqual(mode & 0o077, 0, str(path.relative_to(root)))            # ninguém além do dono lê
            self.assertEqual(stat.S_IMODE(root.stat().st_mode), 0o700)

    def test_public_server_exposes_no_admin_or_backup_route_and_binds_locally(self):
        with self.admin() as (urls, call, raw_get, headers):
            rid = self.make(call)
            public = urls["public"]
            for path in ("/api/backups/overview", "/api/session", "/api/stores", "/api/sites", "/backups.js", f"/api/backups/loja-a/{rid}/download",
                         "/api/backups/_protecao", "/api/backups/_retencao", "/app.js", "/index.html"):
                status, _, body = request(public + path)
                self.assertEqual(status, 404, path)
                self.assertNotIn(b"cdbs_session", body)
            for method in ("POST", "PUT", "DELETE"):
                status, _, _ = request(public + "/api/backups/loja-a/create", method, json_body={"password": PW}, headers={"Origin": public, "X-CDBS-Request": "1", "Cookie": headers["Cookie"]})
                self.assertIn(status, (404, 405))
            self.assertEqual(public.split("//")[1].split(":")[0], "127.0.0.1")
            self.assertEqual(urls["admin"].split("//")[1].split(":")[0], "127.0.0.1")

    def test_protection_setup_over_http_rules(self):
        with self.admin() as (urls, call, raw_get, headers):
            status, data = call("POST", "/api/backups/_protecao/setup", {"password": PW, "confirm": PW})
            self.assertEqual((status, data["code"]), (409, "PROTECAO_JA_CONFIGURADA"))      # já configurada pelo admin() de teste
            status, data = call("GET", "/api/backups/_protecao")
            self.assertTrue(data["protection"]["configured"])
            self.assertNotIn("check", json.dumps(data))


# a subclasse herdaria os testes de HttpTests e os repetiria: ficam só os novos
for _name in [n for n in dir(tb.HttpTests) if n.startswith("test_")]:
    setattr(RouteAuditTests, _name, None)
del _name


if __name__ == "__main__":
    unittest.main()
