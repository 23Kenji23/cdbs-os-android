"""Testes do catálogo: dados, painel do cliente (/_painel no servidor dos sites), API pública e gestão pelo admin."""
import contextlib
import http.client
import json
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

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import catalog
import cdbs

JPEG = b"\xff\xd8\xff\xe0" + b"0" * 64
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64
WEBP = b"RIFF\x00\x00\x00\x00WEBP" + b"0" * 64


def request(url, method="GET", body=None, headers=None, json_body=None):
    headers = dict(headers or {})
    if json_body is not None:
        body = json.dumps(json_body).encode()
        headers.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            return response.status, {k.lower(): v for k, v in response.getheaders()}, response.read()
    except urllib.error.HTTPError as error:
        return error.code, {k.lower(): v for k, v in error.headers.items()}, error.read()


@contextlib.contextmanager
def stack():
    """Sobe os 2 servidores (sites e admin) numa pasta temporária. O painel do cliente mora em <sites>/_painel."""
    with tempfile.TemporaryDirectory() as folder, contextlib.ExitStack() as stop:
        root = Path(folder)
        (root / "sites").mkdir()
        stop.enter_context(patch.object(cdbs, "DATA", root))
        stop.enter_context(patch.object(cdbs, "SITES", root / "sites"))
        stop.enter_context(patch.object(cdbs, "AUTH_FILE", root / "admin.json"))
        stop.enter_context(patch.object(cdbs, "CLIENT_LOGIN_LIMIT", cdbs.RateLimiter(10, 300)))
        servers = {}
        for name, handler in (("public", cdbs.PublicHandler), ("admin", cdbs.AdminHandler)):
            server = cdbs.LocalOnlyServer(("127.0.0.1", 0), handler)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            servers[name] = server
        stop.enter_context(patch.object(cdbs, "ADMIN_PORT", servers["admin"].server_port))
        try:
            urls = {name: f"http://127.0.0.1:{server.server_port}" for name, server in servers.items()}
            urls["client"] = urls["public"] + "/_painel"
            yield urls
        finally:
            for server in servers.values():
                server.shutdown()
                server.server_close()


class Client:
    """Navegador de mentirinha do cliente da loja."""

    def __init__(self, base, slug, password):
        self.base, self.cookie = base, ""
        self.origin = origin_of(base)
        status, headers, _ = request(base + "/api/login", "POST", json_body={"slug": slug, "password": password}, headers={"Origin": self.origin})
        self.login_status = status
        if status == 200:
            self.cookie = headers["set-cookie"].split(";", 1)[0]

    def call(self, method, path, json_body=None, body=None, headers=None):
        sent = {"Origin": self.origin, "Cookie": self.cookie, **(headers or {})}
        status, out_headers, data = request(self.base + path, method, body=body, headers=sent, json_body=json_body)
        try:
            return status, json.loads(data)
        except ValueError:
            return status, data


def no_redirect(url, method="GET"):
    """Pedido sem seguir redirecionamento (o urllib segue sozinho)."""
    host, port = url.split("/")[2].split(":")
    connection = http.client.HTTPConnection(host, int(port), timeout=15)
    connection.request(method, "/" + url.split("/", 3)[3])
    response = connection.getresponse()
    result = response.status, {k.lower(): v for k, v in response.getheaders()}, response.read()
    connection.close()
    return result


def origin_of(url):
    """O navegador manda só esquema://host:porta no Origin, sem o caminho."""
    return "/".join(url.split("/")[:3])


def new_store(slug="loja-a", name="Loja A", whatsapp="(85) 99999-9999"):
    return cdbs.get_catalog().create_store(slug, name, whatsapp)


class DataLayerTests(unittest.TestCase):
    def test_price_parsing(self):
        for text, cents in (("89,90", 8990), ("89.90", 8990), ("1.234,56", 123456), ("R$ 12", 1200), ("1.234", 123400), ("0", 0), ("", None)):
            self.assertEqual(catalog.parse_price(text), cents, text)
        for bad in ("abc", "-1", "1e9", "NaN", "Infinity", True, [1], {"a": 1}):
            with self.subTest(bad=bad), self.assertRaises(catalog.CatalogError):
                catalog.parse_price(bad)
        self.assertEqual(catalog.format_price(123456), "R$ 1.234,56")

    def test_whatsapp_normalization(self):
        self.assertEqual(catalog.normalize_whatsapp("(85) 99999-9999"), "5585999999999")
        self.assertEqual(catalog.normalize_whatsapp("+55 85 99999-9999"), "5585999999999")
        self.assertEqual(catalog.normalize_whatsapp(""), "")
        with self.assertRaises(catalog.CatalogError):
            catalog.normalize_whatsapp("12345")

    def test_password_is_typed_forgivingly_and_stored_hashed(self):
        with tempfile.TemporaryDirectory() as folder:
            cat = catalog.Catalog(Path(folder) / "c", Path(folder) / "secret.key")
            password = cat.create_store("loja", "Loja", "")
            self.assertRegex(password, r"^[A-Z2-9]{4}(-[A-Z2-9]{4}){3}$")
            self.assertTrue(cat.verify_login("loja", password.lower().replace("-", " ")))
            self.assertFalse(cat.verify_login("loja", "ERRADA"))
            self.assertFalse(cat.verify_login("nao-existe", password))
            self.assertNotIn(password, (Path(folder) / "c" / "loja" / "store.json").read_text())
            self.assertEqual((Path(folder) / "c" / "loja" / "store.json").stat().st_mode & 0o077, 0)

    def test_session_survives_restart_and_dies_on_password_reset(self):
        with tempfile.TemporaryDirectory() as folder:
            files = (Path(folder) / "c", Path(folder) / "secret.key")
            cookie = None
            first = catalog.Catalog(*files)
            first.create_store("loja", "Loja", "")
            cookie = first.make_session("loja")
            second = catalog.Catalog(*files)       # "reiniciou": nova instância, mesmo disco
            self.assertEqual(second.read_session(cookie), "loja")
            self.assertIsNone(second.read_session(cookie[:-1] + ("0" if cookie[-1] != "0" else "1")))
            self.assertIsNone(second.read_session("loja.9999999999.1.deadbeef"))
            with patch.object(catalog, "SESSION_SECONDS", -10):
                self.assertIsNone(second.read_session(second.make_session("loja")))
            second.reset_password("loja")
            self.assertIsNone(second.read_session(cookie))

    def test_concurrent_adds_do_not_lose_products(self):
        with tempfile.TemporaryDirectory() as folder:
            cat = catalog.Catalog(Path(folder) / "c", Path(folder) / "secret.key")
            cat.create_store("loja", "Loja", "")
            with ThreadPoolExecutor(max_workers=12) as pool:
                list(pool.map(lambda n: catalog.Catalog(*[Path(folder) / "c", Path(folder) / "secret.key"]).add_product("loja", {"name": f"P{n}"}), range(30)))
            items = cat.products("loja")
            self.assertEqual(len(items), 30)
            self.assertEqual(len({item["id"] for item in items}), 30)


class ClientPanelTests(unittest.TestCase):
    def test_login_and_session(self):
        with stack() as urls:
            password = new_store()
            base = urls["client"]
            self.assertEqual(request(base + "/api/products")[0], 401)
            wrong = Client(base, "loja-a", "ERRADA-ERRADA-1234")
            unknown = Client(base, "nao-existe", password)
            self.assertEqual((wrong.login_status, unknown.login_status), (401, 401))
            client = Client(base, "  Loja-A ", password.lower())   # tolera espaços e maiúsculas
            self.assertEqual(client.login_status, 200)
            self.assertEqual(client.call("GET", "/api/session")[1]["store"]["name"], "Loja A")
            client.cookie = client.cookie[:-3] + "abc"             # cookie adulterado
            self.assertEqual(client.call("GET", "/api/products")[0], 401)

    def test_cookie_flags_and_https_behind_tunnel(self):
        with stack() as urls:
            password = new_store()
            base = urls["client"]
            _, headers, _ = request(base + "/api/login", "POST", json_body={"slug": "loja-a", "password": password}, headers={"Origin": origin_of(base)})
            cookie = headers["set-cookie"]
            for flag in ("HttpOnly", "SameSite=Strict", "Path=/_painel;"):   # o cookie nunca vai para as páginas dos sites
                self.assertIn(flag, cookie)
            self.assertNotIn("Secure", cookie)
            _, headers, _ = request(base + "/api/login", "POST", json_body={"slug": "loja-a", "password": password},
                                    headers={"Origin": origin_of(base), "X-Forwarded-Proto": "https"})
            self.assertIn("Secure", headers["set-cookie"])
            self.assertIn("max-age", headers["strict-transport-security"])

    def test_writes_require_matching_origin(self):
        with stack() as urls:
            password = new_store()
            client = Client(urls["client"], "loja-a", password)
            payload = json.dumps({"name": "X"}).encode()
            for origin in (None, "http://evil.example", "http://127.0.0.1:1"):
                headers = {"Cookie": client.cookie, "Content-Type": "application/json"}
                if origin:
                    headers["Origin"] = origin
                with self.subTest(origin=origin):
                    self.assertEqual(request(urls["client"] + "/api/products", "POST", body=payload, headers=headers)[0], 403)
            self.assertEqual(client.call("GET", "/api/products")[1]["products"], [])

    def test_login_is_rate_limited_per_ip_and_only_failures_count(self):
        with stack() as urls:
            password = new_store()
            base = urls["client"]

            def attempt(ip, pw):
                return request(base + "/api/login", "POST", json_body={"slug": "loja-a", "password": pw},
                               headers={"Origin": origin_of(base), "CF-Connecting-IP": ip})[0]
            for _ in range(5):                       # logins certos não gastam tentativas
                self.assertEqual(attempt("1.1.1.1", password), 200)
            with ThreadPoolExecutor(max_workers=12) as pool:
                statuses = list(pool.map(lambda n: attempt("2.2.2.2", f"ERRADA-{n}"), range(14)))
            self.assertEqual((statuses.count(401), statuses.count(429)), (10, 4))
            self.assertEqual(attempt("2.2.2.2", password), 429)       # bloqueado, mesmo com a senha certa
            self.assertEqual(attempt("3.3.3.3", password), 200)       # outro IP não é afetado

    def test_product_crud_and_validation(self):
        with stack() as urls:
            client = Client(urls["client"], "loja-a", new_store())
            status, data = client.call("POST", "/api/products", {"name": "  Camiseta   preta ", "price": "89,9", "category": "Camisetas", "description": "Algodão"})
            self.assertEqual(status, 201)
            product = data["product"]
            self.assertEqual((product["name"], product["price_cents"], product["price_text"], product["price_input"]), ("Camiseta preta", 8990, "R$ 89,90", "89,90"))
            status, data = client.call("PUT", f"/api/products/{product['id']}", {"available": False})
            self.assertEqual((status, data["product"]["available"], data["product"]["name"]), (200, False, "Camiseta preta"))
            for bad in ({"name": ""}, {"name": "x" * 81}, {"name": "ok", "price": "abc"}, {"name": 5}, {"name": "ok", "available": "sim"}, {"name": "ok", "description": "x" * 501}):
                with self.subTest(bad=bad):
                    self.assertEqual(client.call("POST", "/api/products", bad)[0], 400)
            self.assertEqual(client.call("PUT", "/api/products/ffffffffffff", {"name": "Y"})[0], 404)
            self.assertEqual(client.call("PUT", "/api/products/../../x", {"name": "Y"})[0], 404)
            status, data = client.call("GET", "/api/products")
            self.assertEqual([item["name"] for item in data["products"]], ["Camiseta preta"])
            self.assertEqual(data["categories"], ["Camisetas"])
            self.assertEqual(client.call("DELETE", f"/api/products/{product['id']}")[0], 200)
            self.assertEqual(client.call("GET", "/api/products")[1]["products"], [])

    def test_product_limit(self):
        with stack() as urls, patch.object(catalog, "MAX_PRODUCTS", 2):
            client = Client(urls["client"], "loja-a", new_store())
            self.assertEqual([client.call("POST", "/api/products", {"name": n})[0] for n in "abc"], [201, 201, 409])

    def test_images_are_checked_by_content_not_by_name_or_type(self):
        with stack() as urls:
            client = Client(urls["client"], "loja-a", new_store())
            pid = client.call("POST", "/api/products", {"name": "Boné"})[1]["product"]["id"]
            path = f"/api/products/{pid}/image"
            for payload in (b"<html><script>alert(1)</script></html>", b"GIF89a....", b"<svg xmlns='http://www.w3.org/2000/svg'/>", b""):
                status, _ = client.call("PUT", path, body=payload, headers={"Content-Type": "image/jpeg"})
                self.assertIn(status, (400, 413), payload)
            with patch.object(catalog, "MAX_IMAGE_BYTES", 100):
                self.assertEqual(client.call("PUT", path, body=JPEG + b"0" * 100)[0], 413)
            for payload, extension in ((JPEG, "jpg"), (PNG, "png"), (WEBP, "webp")):
                status, data = client.call("PUT", path, body=payload, headers={"Content-Type": "text/html"})
                self.assertEqual(status, 200)
                self.assertTrue(data["product"]["image"].endswith("." + extension))

    def test_image_files_are_replaced_and_removed(self):
        with stack() as urls:
            client = Client(urls["client"], "loja-a", new_store())
            media = Path(cdbs.DATA) / "catalogs" / "loja-a" / "media"
            pid = client.call("POST", "/api/products", {"name": "Boné"})[1]["product"]["id"]
            client.call("PUT", f"/api/products/{pid}/image", body=JPEG)
            first = {item.name for item in media.iterdir()}
            client.call("PUT", f"/api/products/{pid}/image", body=PNG)
            second = {item.name for item in media.iterdir()}
            self.assertEqual(len(first), 1)
            self.assertEqual(len(second), 1)
            self.assertNotEqual(first, second)                  # a foto antiga foi apagada
            self.assertEqual(client.call("DELETE", f"/api/products/{pid}/image")[1]["product"]["image"], None)
            self.assertEqual(list(media.iterdir()), [])
            client.call("PUT", f"/api/products/{pid}/image", body=JPEG)
            client.call("DELETE", f"/api/products/{pid}")
            self.assertEqual(list(media.iterdir()), [])

    def test_stores_cannot_touch_each_other(self):
        with stack() as urls:
            a = Client(urls["client"], "loja-a", new_store("loja-a", "A", ""))
            b = Client(urls["client"], "loja-b", new_store("loja-b", "B", ""))
            pid_b = b.call("POST", "/api/products", {"name": "Produto da B"})[1]["product"]["id"]
            b.call("PUT", f"/api/products/{pid_b}/image", body=JPEG)
            image_b = b.call("GET", "/api/products")[1]["products"][0]["image"]
            self.assertTrue(b.call("GET", "/api/products")[1]["products"][0]["image_url"].startswith("/_painel/media/"))
            self.assertEqual(a.call("PUT", f"/api/products/{pid_b}", {"name": "invadido"})[0], 404)
            self.assertEqual(a.call("DELETE", f"/api/products/{pid_b}")[0], 404)
            self.assertEqual(a.call("PUT", f"/api/products/{pid_b}/image", body=PNG)[0], 404)
            self.assertEqual(a.call("GET", f"/media/{image_b}")[0], 404)
            self.assertEqual(b.call("GET", f"/media/{image_b}")[0], 200)
            self.assertEqual(a.call("GET", "/api/products")[1]["products"], [])
            self.assertEqual(b.call("GET", "/api/products")[1]["products"][0]["name"], "Produto da B")

    def test_assets_and_unknown_routes(self):
        with stack() as urls:
            base = urls["client"]
            status, headers, body = request(base + "/")
            self.assertEqual(status, 200)
            self.assertIn(b"/_painel/app.js", body)
            # Pode abrir por cima de páginas do MESMO endereço (o botão escondido), nunca de outro site.
            self.assertIn("frame-ancestors 'self'", headers["content-security-policy"])
            self.assertEqual(headers["x-frame-options"], "SAMEORIGIN")
            self.assertEqual(headers["cache-control"], "no-store")
            self.assertEqual(request(base + "/app.js")[0], 200)
            self.assertEqual(request(base + "/app.css")[1]["content-type"], "text/css; charset=utf-8")
            for path in ("/etc/passwd", "/api/stores", "/api/sites", "/../cdbs.py", "/catalog.py", "/media/x.jpg",
                         "/../../web/app.js", "/%2e%2e/catalog.py", "//api/session"):
                self.assertIn(request(base + path)[0], (401, 404), path)

    def test_panel_lives_inside_the_sites_server(self):
        with stack() as urls:
            public = urls["public"]
            # /_painel sem barra vira /_painel/ e mantém o ?loja=
            status, headers, _ = no_redirect(public + "/_painel?loja=adega")
            self.assertEqual((status, headers["location"]), (301, "/_painel/?loja=adega"))
            self.assertEqual(no_redirect(public + "/_painel")[1]["location"], "/_painel/")
            self.assertEqual(request(public + "/_painel?loja=adega")[0], 200)     # e o destino abre
            # HEAD não tem corpo; o painel só responde GET/POST/PUT/DELETE
            status, headers, body = no_redirect(public + "/_painel/", "HEAD")
            self.assertEqual((status, body), (405, b""))
            # fora do /_painel o servidor dos sites continua só leitura
            for method, path in (("POST", "/loja-a/"), ("PUT", "/_api/loja-a/products.json"), ("DELETE", "/_media/loja-a/x.jpg"),
                                 ("POST", "/_painelx/api/login"), ("PATCH", "/_painel/api/products"), ("OPTIONS", "/_painel/")):
                with self.subTest(method=method, path=path):
                    self.assertEqual(request(public + path, method, body=b"{}", headers={"Origin": public})[0], 405)
            # nenhum site pode se chamar _painel
            self.assertFalse(cdbs.valid_slug("_painel"))
            self.assertEqual(request(public + "/_painelx/")[0], 404)

    def test_hidden_button_helper_is_served(self):
        with stack() as urls:
            status, headers, body = request(urls["public"] + "/_cdbs/admin.js")
            self.assertEqual(status, 200)
            self.assertTrue(headers["content-type"].startswith("text/javascript"))
            self.assertEqual(headers["x-content-type-options"], "nosniff")
            for needle in (b"data-cdbs-admin", b"/_painel/?loja=", b"cdbs:atualizado", b"#painel"):
                self.assertIn(needle, body)
            self.assertIn(b"Object.assign(global.CDBS || {}", request(urls["public"] + "/_cdbs/catalog.js")[2])


class PublicApiTests(unittest.TestCase):
    def test_products_json_is_public_and_leaks_nothing_private(self):
        with stack() as urls:
            password = new_store()
            client = Client(urls["client"], "loja-a", password)
            client.call("POST", "/api/products", {"name": "<script>alert(1)</script>", "price": "10", "category": "Cat"})
            pid = client.call("GET", "/api/products")[1]["products"][0]["id"]
            client.call("PUT", f"/api/products/{pid}/image", body=JPEG)
            status, headers, body = request(urls["public"] + "/_api/loja-a/products.json")
            self.assertEqual(status, 200)
            self.assertEqual(headers["content-type"], "application/json; charset=utf-8")
            self.assertEqual(headers["access-control-allow-origin"], "*")
            for secret in (password, "salt", "hash", "epoch", "password"):
                self.assertNotIn(secret.encode(), body)
            data = json.loads(body)
            self.assertEqual(data["store"], {"slug": "loja-a", "name": "Loja A", "whatsapp": "5585999999999", "whatsapp_url": "https://wa.me/5585999999999"})
            product = data["products"][0]
            self.assertEqual(product["name"], "<script>alert(1)</script>")   # dado cru: quem exibe usa textContent
            self.assertEqual((product["price_cents"], product["price_text"], product["available"]), (1000, "R$ 10,00", True))
            self.assertTrue(product["image"].startswith("/_media/loja-a/"))
            self.assertEqual(data["categories"], ["Cat"])

    def test_etag_and_missing_stores(self):
        with stack() as urls:
            new_store()
            url = urls["public"] + "/_api/loja-a/products.json"
            etag = request(url)[1]["etag"]
            self.assertEqual(request(url, headers={"If-None-Match": etag})[0], 304)
            Client(urls["client"], "loja-a", "x")   # (login inválido não altera nada)
            self.assertEqual(request(url, headers={"If-None-Match": '"outro"'})[0], 200)
            for path in ("/_api/nao-existe/products.json", "/_api/loja-a/outra.json", "/_api/../x/products.json",
                         "/_api/LOJA-A/products.json", "/_api/loja-a", "/_api"):
                self.assertEqual(request(urls["public"] + path)[0], 404, path)

    def test_media_is_served_safely_and_cached(self):
        with stack() as urls:
            client = Client(urls["client"], "loja-a", new_store())
            pid = client.call("POST", "/api/products", {"name": "P"})[1]["product"]["id"]
            name = client.call("PUT", f"/api/products/{pid}/image", body=JPEG)[1]["product"]["image"]
            status, headers, body = request(urls["public"] + f"/_media/loja-a/{name}")
            self.assertEqual((status, body), (200, JPEG))
            self.assertEqual(headers["content-type"], "image/jpeg")
            self.assertEqual(headers["x-content-type-options"], "nosniff")
            self.assertIn("immutable", headers["cache-control"])
            self.assertIn("sandbox", headers["content-security-policy"])
            self.assertEqual(request(urls["public"] + f"/_media/loja-a/{name}", headers={"Range": "bytes=0-3"})[0], 206)
            for path in ("/_media/loja-a/store.json", "/_media/loja-a/products.json", "/_media/loja-a/..%2fstore.json",
                         "/_media/loja-a/%2e%2e/store.json", "/_media/outra/" + name, f"/_media/loja-a/{name}.php", "/_media/loja-a/"):
                self.assertEqual(request(urls["public"] + path)[0], 404, path)

    def test_sites_cannot_shadow_reserved_paths_and_helper_is_served(self):
        with stack() as urls:
            self.assertFalse(cdbs.valid_slug("_api"))
            with self.assertRaises(cdbs.CDBSError):
                cdbs.install_site("_api", b"")
            status, headers, body = request(urls["public"] + "/_cdbs/catalog.js")
            self.assertEqual(status, 200)
            self.assertIn(b"CDBS", body)
            self.assertEqual(request(urls["public"] + "/_cdbs/outro.js")[0], 404)


class AdminStoreTests(unittest.TestCase):
    def test_admin_manages_stores(self):
        with stack() as urls:
            admin = urls["admin"]
            headers = {"Origin": admin}
            _, setup_headers, _ = request(admin + "/api/setup", "POST", json_body={"password": "senha-do-admin-123"}, headers=headers)
            headers["Cookie"] = setup_headers["set-cookie"].split(";", 1)[0]

            def call(method, path, body=None, extra=None):
                status, _, data = request(admin + path, method, json_body=body, headers={**headers, **(extra or {})})
                return status, json.loads(data)

            self.assertEqual(request(admin + "/api/stores")[0], 401)
            self.assertEqual(call("POST", "/api/stores", {"slug": "adega", "name": "Adega"}, {"Origin": "http://evil.example"})[0], 403)
            status, data = call("POST", "/api/stores", {"slug": " Adega ", "name": "Adega do Zé", "whatsapp": "85 99999-9999"})
            self.assertEqual(status, 201)
            self.assertEqual(data["slug"], "adega")
            self.assertRegex(data["password"], r"^[A-Z2-9]{4}(-[A-Z2-9]{4}){3}$")
            self.assertEqual(call("POST", "/api/stores", {"slug": "adega", "name": "Outra"})[0], 409)
            self.assertEqual(call("POST", "/api/stores", {"slug": "../x", "name": "Y"})[0], 400)
            self.assertEqual(call("POST", "/api/stores", {"slug": "sem-nome", "name": ""})[0], 400)

            # a senha entregue realmente abre o painel do cliente
            self.assertEqual(Client(urls["client"], "adega", data["password"]).login_status, 200)

            status, listing = call("GET", "/api/stores")
            self.assertEqual(listing["public_base"], "")
            self.assertEqual(listing["local_base"], f"http://127.0.0.1:{cdbs.SITE_PORT}")
            self.assertEqual([(s["slug"], s["name"], s["products"], s["site_published"]) for s in listing["stores"]], [("adega", "Adega do Zé", 0, False)])
            self.assertEqual(call("POST", "/api/stores/adega", {"name": "Adega Nova", "whatsapp": "(85) 98888-7777"})[0], 200)
            self.assertEqual(call("GET", "/api/stores")[1]["stores"][0]["whatsapp"], "5585988887777")

            old = Client(urls["client"], "adega", data["password"])
            new_password = call("POST", "/api/stores/adega/password", {})[1]["password"]
            self.assertNotEqual(new_password, data["password"])
            self.assertEqual(old.call("GET", "/api/products")[0], 401)             # sessão antiga caiu
            self.assertEqual(Client(urls["client"], "adega", data["password"]).login_status, 401)
            self.assertEqual(Client(urls["client"], "adega", new_password).login_status, 200)

            self.assertEqual(call("DELETE", "/api/stores/adega")[0], 200)
            self.assertFalse((Path(cdbs.DATA) / "catalogs" / "adega").exists())
            self.assertEqual(request(urls["public"] + "/_api/adega/products.json")[0], 404)
            self.assertEqual(call("DELETE", "/api/stores/adega")[0], 404)


if __name__ == "__main__":
    unittest.main()
