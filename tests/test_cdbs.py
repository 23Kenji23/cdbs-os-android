import io
import json
import shutil
import stat
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
from zipfile import ZipFile, ZipInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cdbs


def make_zip(files):
    buffer = io.BytesIO()
    with ZipFile(buffer, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def get_status(url, headers=None, method="GET", data=None):
    request = urllib.request.Request(url, headers=headers or {}, method=method, data=data)
    try:
        with urllib.request.urlopen(request) as response:
            response.read()
            return response.status
    except urllib.error.HTTPError as error:
        error.read()
        return error.code


class CDBSTests(unittest.TestCase):
    def test_slug_rules(self):
        self.assertTrue(cdbs.valid_slug("minha-loja-2"))
        self.assertFalse(cdbs.valid_slug("../segredo"))
        self.assertFalse(cdbs.valid_slug("Nome Com Espaço"))
        self.assertEqual(cdbs.slugify("Adega Corujão"), "adega-corujao")

    def test_password_is_hashed_and_verifiable(self):
        with tempfile.TemporaryDirectory() as folder:
            auth = Path(folder) / "admin.json"
            record = cdbs._password_record("uma-senha-de-teste-123")
            auth.write_text(json.dumps(record), encoding="utf-8")
            with patch.object(cdbs, "AUTH_FILE", auth):
                self.assertTrue(cdbs._verify_password("uma-senha-de-teste-123"))
                self.assertFalse(cdbs._verify_password("senha-errada"))
            self.assertNotIn("uma-senha-de-teste-123", json.dumps(record))

    def test_zip_upload_and_public_static_routes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            payload = make_zip({"meu-site/index.html": "<h1>Oi</h1>", "meu-site/css/site.css": "body{}"})
            with patch.object(cdbs, "SITES", root):
                cdbs.install_site("demo", payload)
                server = cdbs.LocalOnlyServer(("127.0.0.1", 0), cdbs.PublicHandler)
                self.assertEqual(server.server_address[0], "127.0.0.1")
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    base = f"http://127.0.0.1:{server.server_port}"
                    with urllib.request.urlopen(base + "/demo/") as response:
                        self.assertEqual(response.status, 200)
                        self.assertIn(b"Oi", response.read())
                    connection = urllib.request.urlopen(urllib.request.Request(base + "/demo/", method="HEAD"))
                    self.assertEqual(connection.status, 200)
                    self.assertEqual(connection.read(), b"")
                    for path in ("/demo/.env", "/demo/%2e%2e/outside", "/api/sites"):
                        self.assertEqual(get_status(base + path), 404)
                    self.assertEqual(get_status(base + "/api/setup", method="POST", data=b"{}"), 405)
                finally:
                    server.shutdown()
                    server.server_close()

    def test_zip_slip_symlink_and_hidden_or_server_files_are_rejected(self):
        symlink = ZipInfo("outside-link")
        symlink.create_system = 3
        symlink.external_attr = (stat.S_IFLNK | 0o777) << 16
        candidates = [
            {"index.html": "ok", "../outside.txt": "bad"},
            {"index.html": "ok", ".env": "secret"},
            {"index.html": "ok", "run.php": "<?php"},
            {"index.html": "ok", symlink: "../../outside"},
            {"index.html": "ok", ".": "bad"},
            {"index.html": "ok", "": "bad"},
        ]
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with patch.object(cdbs, "SITES", root):
                for files in candidates:
                    with self.subTest(files=list(map(str, files))):
                        with self.assertRaises(cdbs.CDBSError):
                            cdbs.install_site("demo", make_zip(files))
                        for child in root.iterdir():
                            if child.is_dir():
                                shutil.rmtree(child)
            self.assertFalse((root.parent / "outside.txt").exists())

    def test_zip_limits_are_enforced(self):
        payload = make_zip({"index.html": "123456", "image.png": "123"})
        with tempfile.TemporaryDirectory() as folder, patch.object(cdbs, "SITES", Path(folder)):
            with patch.object(cdbs, "MAX_ZIP", 5):
                with self.assertRaises(cdbs.CDBSError):
                    cdbs.install_site("zip-limit", payload)
            with patch.object(cdbs, "MAX_UNPACKED", 5):
                with self.assertRaises(cdbs.CDBSError):
                    cdbs.install_site("unpacked-limit", payload)
            with patch.object(cdbs, "MAX_FILES", 1):
                with self.assertRaises(cdbs.CDBSError):
                    cdbs.install_site("file-limit", payload)

    def test_empty_directory_member_is_ignored(self):
        payload = make_zip({"./": b"", "index.html": "ok"})
        with tempfile.TemporaryDirectory() as folder, patch.object(cdbs, "SITES", Path(folder)):
            cdbs.install_site("demo", payload)
            self.assertEqual((Path(folder) / "demo" / "index.html").read_text(), "ok")

    def test_duplicate_uploads_return_conflict_not_server_error(self):
        payload = make_zip({"index.html": "ok"})
        with tempfile.TemporaryDirectory() as folder, patch.object(cdbs, "SITES", Path(folder)):
            def upload():
                try:
                    cdbs.install_site("same-name", payload)
                    return 201
                except cdbs.CDBSError as error:
                    return error.status
            with ThreadPoolExecutor(max_workers=2) as pool:
                statuses = sorted(pool.map(lambda _: upload(), range(2)))
            self.assertEqual(statuses, [201, 409])

    def test_admin_setup_upload_delete_origin_and_session(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            auth_file = root / "admin.json"
            sites = root / "sites"
            sites.mkdir()
            with patch.object(cdbs, "AUTH_FILE", auth_file), patch.object(cdbs, "DATA", root), patch.object(cdbs, "SITES", sites):
                server = cdbs.LocalOnlyServer(("127.0.0.1", 0), cdbs.AdminHandler)
                port = server.server_port
                base = f"http://127.0.0.1:{port}"
                with patch.object(cdbs, "ADMIN_PORT", port):
                    thread = threading.Thread(target=server.serve_forever, daemon=True)
                    thread.start()
                    try:
                        self.assertEqual(get_status(base + "/", method="GET"), 200)
                        self.assertEqual(get_status(base + "/api/setup", {"Origin": "http://evil"}, "POST", b"{}"), 403)
                        self.assertFalse(auth_file.exists())

                        def post_json(path, data, cookie=""):
                            headers = {"Origin": base, "Content-Type": "application/json"}
                            if cookie:
                                headers["Cookie"] = cookie
                            request = urllib.request.Request(base + path, data=json.dumps(data).encode(), headers=headers, method="POST")
                            return urllib.request.urlopen(request)

                        with post_json("/api/setup", {"password": "senha-segura-de-teste-123"}) as response:
                            cookie = response.headers.get("Set-Cookie", "")
                            self.assertIn("HttpOnly", cookie)
                            self.assertIn("SameSite=Strict", cookie)
                            cookie = cookie.split(";", 1)[0]
                        token = cookie.split("=", 1)[1]

                        payload = make_zip({"index.html": "<title>Meu site</title>"})
                        request = urllib.request.Request(base + "/api/sites?slug=meu-site", data=payload,
                            headers={"Origin": base, "Content-Type": "application/zip", "Cookie": cookie}, method="POST")
                        with urllib.request.urlopen(request) as response:
                            result = json.loads(response.read())
                            self.assertEqual(response.status, 201)
                            self.assertIn("/meu-site/", result["local_url"])
                            self.assertEqual(result["public_url"], "")

                        request = urllib.request.Request(base + "/api/sites", headers={"Cookie": cookie})
                        with urllib.request.urlopen(request) as response:
                            self.assertEqual([x["slug"] for x in json.loads(response.read())["sites"]], ["meu-site"])

                        request = urllib.request.Request(base + "/api/sites/meu-site", headers={"Origin": base, "Cookie": cookie}, method="DELETE")
                        with urllib.request.urlopen(request) as response:
                            self.assertEqual(response.status, 200)
                        self.assertFalse((sites / "meu-site").exists())

                        self.assertEqual(get_status(base + "/api/sites/meu-site", {"Origin": "http://evil", "Cookie": cookie}, "DELETE"), 403)
                        with post_json("/api/logout", {}, cookie) as response:
                            self.assertIn("Max-Age=0", response.headers.get("Set-Cookie", ""))
                        self.assertEqual(get_status(base + "/api/sites", {"Cookie": cookie}), 401)

                        with post_json("/api/login", {"password": "senha-segura-de-teste-123"}) as response:
                            cookie = response.headers.get("Set-Cookie", "").split(";", 1)[0]
                        cdbs.SESSIONS[cookie.split("=", 1)[1]] = time.time() - 1
                        self.assertEqual(get_status(base + "/api/sites", {"Cookie": cookie}), 401)
                        cdbs.SESSIONS.pop(token, None)
                    finally:
                        server.shutdown()
                        server.server_close()


if __name__ == "__main__":
    unittest.main()
