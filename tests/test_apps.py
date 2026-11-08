"""Servidores (apps) — 4.0: cadastro, ZIP, variáveis, execução, vigilância, implantação com volta e remoção.

Os programas testados são servidores Python DE VERDADE (iniciados pelo AppRunner, como o supervisor faz)."""
import io
import json
import os
import socket
import sys
import tempfile
import time
import unittest
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import apps  # noqa: E402

APP_OK = r'''
import http.server, json, os, sys
MODE = os.environ.get("MODO", "ok")
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        if self.path == "/saude":
            code = 500 if MODE == "doente" else 200
            self.send_response(code); self.end_headers(); self.wfile.write(b"x"); return
        body = json.dumps({"versao": VERSAO, "path": self.path, "base": os.environ.get("CDBS_BASE_PATH"), "dados": os.environ.get("CDBS_APP_DATA"),
                           "segredo": os.environ.get("API_KEY", ""), "cdbs": sorted(k for k in os.environ if k.startswith("CDBS_") and k not in
                           ("CDBS_APP_NAME", "CDBS_BASE_PATH", "CDBS_APP_DATA"))}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(body)
print("subindo na porta", os.environ["PORT"], flush=True)
http.server.ThreadingHTTPServer((os.environ["HOST"], int(os.environ["PORT"])), H).serve_forever()
'''
APP_CRASH = 'import sys\nprint("erro de propósito: faltou a variável DATABASE_URL", flush=True)\nsys.exit(3)\n'


def free_base() -> int:
    """Cinco portas livres seguidas (os testes não podem depender de 8101 estar livre)."""
    for base in range(23100, 24000, 10):
        ok = True
        for p in range(base, base + 5):
            with socket.socket() as s:
                try:
                    s.bind(("127.0.0.1", p))
                except OSError:
                    ok = False
                    break
        if ok:
            return base
    raise RuntimeError("sem portas livres")


def zip_of(files: dict, top: str = "") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, content in files.items():
            z.writestr(top + name, content)
    return buf.getvalue()


def get(port: int, path: str = "/") -> dict:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(f"http://127.0.0.1:{port}{path}", timeout=3) as r:
        return json.loads(r.read())


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name) / "data"
        self.data.mkdir()
        self.base = free_base()
        self.store = apps.AppStore(self.data, environ={"CDBS_APP_PORT_BASE": str(self.base)})
        self.events = []
        self.runner = apps.AppRunner(self.store, record=lambda c, a, r="ok", **f: self.events.append((a, r, f.get("target"))),
                                     say=lambda _t: None, restart_delays=(0.2, 0.2, 0.2), max_crashes=3, crash_window=60,
                                     health_every=0.3, health_fails=2, start_grace=6)

    def tearDown(self):
        self.runner.stop_all("fim do teste")
        self.tmp.cleanup()

    def upload(self, name, files, top=""):
        payload = zip_of(files, top)
        return self.store.stage_zip(name, io.BytesIO(payload), len(payload))

    def wait(self, cond, timeout=8.0):
        end = time.time() + timeout
        while time.time() < end:
            self.runner.tick()
            if cond():
                return True
            time.sleep(0.1)
        return False


class StoreTests(Base):
    def test_create_validates_names_types_slots_and_collision_with_sites(self):
        bad = [("A B", "python", "app.py"), ("../x", "python", "app.py"), ("api", "python", "app.py"), ("ok", "ruby", "app.rb"),
               ("ok", "python", "app.js"), ("ok", "node", "server.py"), ("ok", "python", "/etc/app.py"), ("ok", "python", "../app.py")]
        for args in bad:
            with self.assertRaises(apps.AppError, msg=args):
                self.store.create(*args)
        with self.assertRaises(apps.AppError) as ctx:
            self.store.create("adega", "python", "app.py", taken=lambda n: n == "adega")
        self.assertIn("SITE", str(ctx.exception))
        created = [self.store.create(f"app{n}", "python", "app.py") for n in range(1, 6)]
        self.assertEqual([c["slot"] for c in created], [1, 2, 3, 4, 5])
        self.assertEqual([c["port"] for c in created], list(range(self.base, self.base + 5)))
        with self.assertRaises(apps.AppError) as ctx:
            self.store.create("app6", "python", "app.py")
        self.assertIn("5 vagas", str(ctx.exception))
        with self.assertRaises(apps.AppError):
            self.store.create("app1", "python", "app.py")
        self.store.remove("app2")
        self.assertEqual(self.store.create("nova", "node", "server.js")["slot"], 2, "a vaga liberada é reaproveitada")
        self.assertTrue(list((self.data / "removidos" / "apps").iterdir()), "remover guarda os arquivos")

    def test_zip_is_validated_and_a_single_top_folder_is_removed(self):
        self.store.create("loja", "python", "app.py")
        info = self.upload("loja", {"app.py": "print(1)", "lib/util.py": "x=1", "__MACOSX/._app.py": "lixo"}, top="meu-projeto/")
        self.assertEqual(info["files"], 2)
        self.assertTrue((self.data / "apps/loja/novo/app.py").is_file())
        self.assertFalse((self.data / "apps/loja/novo/__MACOSX").exists())
        with self.assertRaises(apps.AppError) as ctx:
            self.upload("loja", {"outro.py": "print(1)"})
        self.assertIn("arquivo de entrada", str(ctx.exception))
        with self.assertRaises(apps.AppError):
            self.upload("loja", {"../fora.py": "x", "app.py": "x"})
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("app.py", "x")
            link = zipfile.ZipInfo("atalho")
            link.external_attr = (0o120777 << 16)
            z.writestr(link, "/etc/passwd")
        with self.assertRaises(apps.AppError) as ctx:
            self.store.stage_zip("loja", io.BytesIO(buf.getvalue()), len(buf.getvalue()))
        self.assertIn("atalho", str(ctx.exception))
        with self.assertRaises(apps.AppError):
            self.store.stage_zip("loja", io.BytesIO(b"nao e zip"), 9)
        self.assertTrue((self.data / "apps/loja/novo/app.py").is_file(), "um envio recusado não apaga o anterior que estava esperando")
        self.assertEqual(list((self.data / "apps/loja").glob(".novo-*")), [], "nada de pasta temporária sobrando")

    def test_env_values_never_return_and_protected_names_are_refused(self):
        self.store.create("api-loja", "python", "app.py")
        self.assertEqual(self.store.set_env("api-loja", {"API_KEY": "segredo-123", "MODO": "ok"}), ["API_KEY", "MODO"])
        view = self.store.get("api-loja")
        self.assertEqual(view["env_keys"], ["API_KEY", "MODO"])
        self.assertNotIn("segredo-123", json.dumps(view))
        for bad in ({"PORT": "1"}, {"CDBS_ADMIN_PORT": "1"}, {"minusculo": "x"}, {"LD_PRELOAD": "x"}, {"A": "x" * 5000}):
            with self.assertRaises(apps.AppError, msg=bad):
                self.store.set_env("api-loja", bad)
        self.store.set_env("api-loja", {}, remove=["MODO"])
        self.assertEqual(self.store.get("api-loja")["env_keys"], ["API_KEY"])
        self.assertEqual(oct((self.data / "apps/api-loja/env.json").stat().st_mode & 0o777), "0o600")


class RunnerTests(Base):
    def deploy_ok(self, name="loja-api", versao=1, env=None):
        if name not in self.store.names():
            self.store.create(name, "python", "app.py", health_path="/saude")
        if env:
            self.store.set_env(name, env)
        self.upload(name, {"app.py": f"VERSAO = {versao}\n" + APP_OK})
        return self.runner.op_deploy(name, pump=lambda: None)

    def test_deploy_runs_with_its_own_port_env_data_folder_and_log(self):
        os.environ["CDBS_TUNNEL_TOKEN"] = "nao-deve-vazar"
        try:
            status, msg, _ = self.deploy_ok(env={"API_KEY": "k1"})
        finally:
            os.environ.pop("CDBS_TUNNEL_TOKEN", None)
        self.assertEqual(status, "ok", msg)
        info = get(self.base, "/x?y=1")
        self.assertEqual(info["versao"], 1)
        self.assertEqual(info["base"], "/loja-api")
        self.assertEqual(info["segredo"], "k1")
        self.assertEqual(info["cdbs"], [], "nenhuma variável do CDBS (tokens!) chega ao servidor")
        self.assertTrue(info["dados"].endswith("apps/loja-api/dados"))
        view = self.runner.runtime_view()["loja-api"]
        self.assertEqual(view["state"], "rodando")
        self.assertIsNotNone(view["pid"])
        self.assertIsNotNone(view["rss_kb"])
        self.assertTrue(any("subindo na porta" in l for l in self.store.log_tail("loja-api")))
        self.assertTrue(self.store.get("loja-api")["enabled"])
        self.assertIn(("app_implantado", "ok", "loja-api"), self.events)

    def test_new_version_that_fails_health_is_rolled_back_automatically(self):
        self.assertEqual(self.deploy_ok(versao=1)[0], "ok")
        self.store.set_env("loja-api", {"MODO": "doente"})
        self.upload("loja-api", {"app.py": "VERSAO = 2\n" + APP_OK})
        status, msg, extra = self.runner.op_deploy("loja-api", pump=lambda: None)
        self.assertEqual(status, "falhou")
        self.assertIn("anterior foi recolocada", msg)
        self.store.set_env("loja-api", {}, remove=["MODO"])
        self.runner.op_start("loja-api", pump=lambda: None)
        self.assertEqual(get(self.base)["versao"], 1, "a versão que funcionava voltou")
        self.assertIn(("app_implantacao_revertida", "falha", "loja-api"), self.events)

    def test_rollback_and_stop_and_remove(self):
        self.deploy_ok(versao=1)
        self.deploy_ok(versao=2)
        self.assertEqual(get(self.base)["versao"], 2)
        self.assertEqual(self.runner.op_deploy("loja-api", pump=lambda: None, rollback=True)[0], "ok")
        self.assertEqual(get(self.base)["versao"], 1)
        self.assertEqual(self.runner.op_stop("loja-api")[0], "ok")
        self.assertFalse(self.store.get("loja-api")["enabled"])
        with self.assertRaises(OSError):
            get(self.base)
        self.runner.tick()
        self.assertNotIn("loja-api", self.runner.procs, "desligado continua desligado")
        self.assertEqual(self.runner.op_remove("loja-api")[0], "ok")
        self.assertNotIn("loja-api", self.store.names())

    def test_crash_loop_restarts_with_backoff_then_stops_for_good(self):
        self.store.create("quebra", "python", "app.py")
        self.upload("quebra", {"app.py": APP_CRASH})
        status, msg, _ = self.runner.op_deploy("quebra", pump=lambda: None)
        self.assertEqual(status, "falhou")
        self.assertIn("DATABASE_URL", msg, "o motivo real aparece (última linha da saída)")
        self.store.set_enabled("quebra", True)                     # a pessoa liga mesmo assim: o vigia tenta e desiste
        self.assertTrue(self.wait(lambda: self.runner.runtime_view()["quebra"]["state"] == "parado_por_falhas", 15))
        view = self.runner.runtime_view()["quebra"]
        self.assertIn("falhas", view["message"])
        self.assertIn(("app_parado_por_falhas", "falha", "quebra"), self.events)
        self.assertTrue(any(a == "app_caiu" for a, _r, _t in self.events))
        time.sleep(0.5)
        self.runner.tick()
        self.assertNotIn("quebra", self.runner.procs, "parado por falhas não volta a tentar sozinho")

    def test_watchdog_restarts_a_process_that_dies_and_one_server_never_affects_another(self):
        self.deploy_ok("a", 1)
        self.deploy_ok("b", 1)
        pid = self.runner.procs["a"].pid
        os.kill(pid, 9)
        self.assertTrue(self.wait(lambda: "a" in self.runner.procs and self.runner.procs["a"].pid != pid
                                  and self.runner.runtime_view()["a"]["state"] == "rodando"))
        self.assertEqual(get(self.base + 1)["versao"], 1, "o servidor b nem percebeu")
        self.assertEqual(self.runner.runtime_view()["a"]["restarts_recent"], 1)

    def test_orphans_from_a_previous_supervisor_are_cleaned(self):
        self.deploy_ok("a", 1)
        self.runner.write_state()
        pid = self.runner.procs["a"].pid
        fresh = apps.AppRunner(self.store, say=lambda _t: None)
        self.assertEqual(fresh.cleanup_orphans(), [pid])
        self.runner.procs["a"].proc.wait(5)
        self.assertIsNotNone(self.runner.procs["a"].proc.returncode)

    def test_port_used_by_another_program_is_reported(self):
        self.store.create("x", "python", "app.py")
        self.upload("x", {"app.py": "VERSAO=1\n" + APP_OK})
        with socket.socket() as s:
            s.bind(("127.0.0.1", self.base))
            s.listen()
            status, msg, _ = self.runner.op_deploy("x", pump=lambda: None)
        self.assertEqual(status, "falhou")
        self.assertIn("ocupada", msg)

    def test_node_missing_is_explained(self):
        runner = apps.AppRunner(self.store, say=lambda _t: None, node="")
        self.store.create("js", "node", "server.js")
        self.upload("js", {"server.js": "1"})
        self.store.swap_in_staged("js")
        ok, why = runner.start("js")
        self.assertFalse(ok)
        self.assertIn("pkg install nodejs", why)


if __name__ == "__main__":
    unittest.main()
