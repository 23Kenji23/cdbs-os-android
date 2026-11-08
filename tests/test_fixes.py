"""Testes das correções de conexões, entrega de arquivos e compatibilidade de ZIP."""
import contextlib
import http.client
import io
import json
import socket
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cdbs


def make_zip(files):
    buffer = io.BytesIO()
    with ZipFile(buffer, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return buffer.getvalue()


@contextlib.contextmanager
def public_server(files, slug="demo"):
    with tempfile.TemporaryDirectory() as folder, patch.object(cdbs, "SITES", Path(folder)):
        cdbs.install_site(slug, make_zip(files))
        server = cdbs.LocalOnlyServer(("127.0.0.1", 0), cdbs.PublicHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            yield server.server_port
        finally:
            server.shutdown()
            server.server_close()


def fetch(port, path, headers=None, method="GET"):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    connection.request(method, path, headers=headers or {})
    response = connection.getresponse()
    body = response.read()
    headers_out = {key.lower(): value for key, value in response.getheaders()}
    connection.close()
    return response.status, headers_out, body


class ZipCompatibilityTests(unittest.TestCase):
    def test_macos_and_windows_junk_is_ignored(self):
        payload = make_zip({
            "meu-site/index.html": "ok",
            "__MACOSX/meu-site/._index.html": "x",
            "meu-site/.DS_Store": "x",
            "meu-site/Thumbs.db": "x",
        })
        with tempfile.TemporaryDirectory() as folder, patch.object(cdbs, "SITES", Path(folder)):
            cdbs.install_site("demo", payload)
            site = Path(folder) / "demo"
            self.assertEqual((site / "index.html").read_text(), "ok")
            self.assertEqual(sorted(p.name for p in site.iterdir()), ["index.html"])

    def test_other_hidden_files_are_still_rejected(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(cdbs, "SITES", Path(folder)):
            for name in (".env", ".git/config", "pasta/.htpasswd"):
                with self.subTest(name=name), self.assertRaises(cdbs.CDBSError):
                    cdbs.install_site("demo", make_zip({"index.html": "ok", name: "segredo"}))

    def test_backslash_paths_are_normalized_and_still_safe(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(cdbs, "SITES", Path(folder)):
            cdbs.install_site("demo", make_zip({"index.html": "ok", "css\\site.css": "body{}", "img\\": b""}))
            self.assertEqual((Path(folder) / "demo" / "css" / "site.css").read_text(), "body{}")
            for files in ({"index.html": "ok", "..\\fora.txt": "x"},
                          {"index.html": "ok", "C:\\x.txt": "x"},
                          {"index.html": "ok", "a\\b.txt": "1", "a/b.txt": "2"}):
                with self.subTest(files=list(files)), self.assertRaises(cdbs.CDBSError):
                    cdbs.install_site("outro", make_zip(files))


class ServingTests(unittest.TestCase):
    def test_etag_and_conditional_requests(self):
        with public_server({"index.html": "<p>oi</p>"}) as port:
            status, headers, body = fetch(port, "/demo/")
            self.assertEqual(status, 200)
            etag, modified = headers["etag"], headers["last-modified"]
            self.assertEqual(headers["accept-ranges"], "bytes")
            status, _, body = fetch(port, "/demo/", {"If-None-Match": etag})
            self.assertEqual((status, body), (304, b""))
            status, _, _ = fetch(port, "/demo/", {"If-None-Match": '"outro"'})
            self.assertEqual(status, 200)
            status, _, body = fetch(port, "/demo/", {"If-Modified-Since": modified})
            self.assertEqual((status, body), (304, b""))

    def test_range_requests(self):
        with public_server({"index.html": "ok", "d.bin": b"0123456789"}) as port:
            cases = {
                "bytes=2-5": (206, b"2345", "bytes 2-5/10"),
                "bytes=-3": (206, b"789", "bytes 7-9/10"),
                "bytes=7-": (206, b"789", "bytes 7-9/10"),
                "bytes=8-99": (206, b"89", "bytes 8-9/10"),
                "bytes=20-30": (416, b"", "bytes */10"),
            }
            for spec, (status, body, content_range) in cases.items():
                with self.subTest(spec=spec):
                    got_status, headers, got_body = fetch(port, "/demo/d.bin", {"Range": spec})
                    self.assertEqual((got_status, got_body), (status, body))
                    self.assertEqual(headers["content-range"], content_range)
            for spec in ("bytes=0-1,3-4", "bytes=abc", "bytes=5-2", "items=0-1"):
                with self.subTest(spec=spec):
                    status, _, body = fetch(port, "/demo/d.bin", {"Range": spec})
                    self.assertEqual((status, body), (200, b"0123456789"))
            status, _, body = fetch(port, "/demo/d.bin", {"Range": "bytes=0-1", "If-Range": '"velho"'})
            self.assertEqual((status, body), (200, b"0123456789"))
            _, headers, _ = fetch(port, "/demo/d.bin")
            status, _, body = fetch(port, "/demo/d.bin", {"Range": "bytes=0-1", "If-Range": headers["etag"]})
            self.assertEqual((status, body), (206, b"01"))

    def test_charset_is_added_only_when_page_does_not_declare_one(self):
        files = {
            "index.html": "<p>olá</p>",
            "latin.html": '<meta charset="iso-8859-1"><p>x</p>',
            "dados.json": "{}",
            "app.js": "var a = 1;",
            "estilo.css": "body{}",
        }
        with public_server(files) as port:
            self.assertEqual(fetch(port, "/demo/")[1]["content-type"], "text/html; charset=utf-8")
            self.assertEqual(fetch(port, "/demo/latin.html")[1]["content-type"], "text/html")
            self.assertEqual(fetch(port, "/demo/dados.json")[1]["content-type"], "application/json; charset=utf-8")
            self.assertIn("javascript", fetch(port, "/demo/app.js")[1]["content-type"])
            self.assertTrue(fetch(port, "/demo/estilo.css")[1]["content-type"].startswith("text/css"))

    def test_many_simultaneous_slow_downloads_all_succeed(self):
        size = 512 * 1024
        with public_server({"index.html": "ok", "big.bin": b"x" * size}) as port:
            def download(_):
                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
                connection.request("GET", "/demo/big.bin")
                response = connection.getresponse()
                received = 0
                while chunk := response.read(64 * 1024):
                    received += len(chunk)
                    time.sleep(0.01)
                connection.close()
                return received
            with ThreadPoolExecutor(max_workers=40) as pool:
                self.assertEqual(list(pool.map(download, range(40))), [size] * 40)

    def test_full_server_answers_503_instead_of_dropping_the_connection(self):
        with patch.object(cdbs, "MAX_CONNECTIONS", 2), public_server({"index.html": "ok"}) as port:
            idle = [socket.create_connection(("127.0.0.1", port)) for _ in range(2)]
            try:
                time.sleep(0.3)
                status, headers, _ = fetch(port, "/demo/")
                self.assertEqual(status, 503)
                self.assertEqual(headers["retry-after"], "2")
            finally:
                for connection in idle:
                    connection.close()
            time.sleep(0.3)
            self.assertEqual(fetch(port, "/demo/")[0], 200)

    def test_connection_that_never_finishes_its_request_is_closed(self):
        with patch.object(cdbs, "HEADER_DEADLINE", 0.5), public_server({"index.html": "ok"}) as port:
            connection = socket.create_connection(("127.0.0.1", port))
            connection.settimeout(5)
            connection.sendall(b"GET /demo/ HT")  # pedido incompleto, e o servidor tem 30 s de silêncio permitido
            started = time.time()
            try:
                data = connection.recv(100)
            except ConnectionResetError:
                data = b""
            connection.close()
            self.assertEqual(data, b"")
            self.assertLess(time.time() - started, 3)


class LoginLimitTests(unittest.TestCase):
    def test_simultaneous_wrong_passwords_cannot_exceed_the_limit(self):
        with tempfile.TemporaryDirectory() as folder:
            auth = Path(folder) / "admin.json"
            auth.write_text(json.dumps(cdbs._password_record("senha-correta-123")), encoding="utf-8")
            server = cdbs.LocalOnlyServer(("127.0.0.1", 0), cdbs.AdminHandler)
            base = f"http://127.0.0.1:{server.server_port}"
            # 3.0: login grava histórico (data/system/audit.jsonl) e sessão (data/admin-sessions.json) — a pasta de dados
            # também precisa ser a temporária, senão o teste escreveria nos dados reais da instalação.
            with patch.object(cdbs, "AUTH_FILE", auth), patch.object(cdbs, "ADMIN_PORT", server.server_port), \
                    patch.object(cdbs, "DATA", Path(folder)), patch.object(cdbs, "SITES", Path(folder) / "sites"), \
                    patch.object(cdbs, "LOGIN_FAILURES", 0), patch.object(cdbs, "LOGIN_BLOCKED_UNTIL", 0.0):
                threading.Thread(target=server.serve_forever, daemon=True).start()
                try:
                    def attempt(password):
                        request = urllib.request.Request(
                            base + "/api/login", data=json.dumps({"password": password}).encode(),
                            headers={"Origin": base, "Content-Type": "application/json"}, method="POST")
                        try:
                            with urllib.request.urlopen(request) as response:
                                return response.status
                        except urllib.error.HTTPError as error:
                            error.read()
                            return error.code
                    with ThreadPoolExecutor(max_workers=12) as pool:
                        statuses = list(pool.map(attempt, ["errada-%d" % n for n in range(12)]))
                    self.assertEqual(statuses.count(401), 5)
                    self.assertEqual(statuses.count(429), 7)
                    # Bloqueado: nem a senha certa entra até o prazo acabar.
                    self.assertEqual(attempt("senha-correta-123"), 429)
                    # Prazo vencido: a senha certa volta a funcionar.
                    cdbs.LOGIN_BLOCKED_UNTIL = time.time() - 1
                    self.assertEqual(attempt("senha-correta-123"), 200)
                finally:
                    server.shutdown()
                    server.server_close()


if __name__ == "__main__":
    unittest.main()
