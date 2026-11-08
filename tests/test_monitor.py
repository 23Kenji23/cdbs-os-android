"""3.0 / Parte 2.1–2.2 — monitoramento das 10 posições: mapa estável, verificação real (HTTP), estados, falhas
parciais, agendamento sem duplicar, pausa e textos do relatório. Sem internet: o "link público" é um servidor local."""
import json
import socket
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import publication
import settings as settings_mod
import site_monitor


class FakeSettings:
    def __init__(self, **monitor):
        self.data = {"monitor": {"enabled": True, "interval_minutes": 30, "public_check": False, **monitor}}

    def get(self):
        return json.loads(json.dumps(self.data))


class SiteServer:
    """Servidor local que faz o papel do servidor dos sites (ou do link público). `routes[path] = (status, corpo)` ou 'lento'."""

    def __init__(self):
        self.routes = {}
        self.hits = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                return

            def do_GET(self):
                outer.hits.append(self.path)
                route = outer.routes.get(self.path, (404, b"nao"))
                if route == "lento":
                    time.sleep(2)
                    route = (200, b"<h1>tarde</h1>")
                status, body = route
                try:
                    self.send_response(status)
                    self.send_header("Content-Type", "text/html")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass                                  # o monitor desistiu antes (timeout): é o esperado

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_port
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Env:
    def __init__(self, test, sites=(), **monitor):
        self.folder = tempfile.TemporaryDirectory()
        test.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.sites = self.root / "sites"
        self.sites.mkdir()
        for slug in sites:
            self.publish(slug)
        self.server = SiteServer()
        test.addCleanup(self.server.close)
        self.public = SiteServer()
        test.addCleanup(self.public.close)
        self.settings = FakeSettings(**monitor)
        self.pub = publication.Publication(self.root / "system" / "publication.json")
        self.maintenance = set()
        self.public_base = ""
        self.rounds = []
        self.clock = [1_761_480_000.0]
        self.monitor = site_monitor.Monitor(
            self.root, self.sites, site_port=lambda: self.server.port, publication=self.pub, maintenance=lambda s: s in self.maintenance,
            settings=self.settings, public_base=lambda: self.public_base, on_round=lambda r, c, s: self.rounds.append((r, c, s)),
            clock=lambda: self.clock[0], local_timeout=1.0, public_timeout=1.0, retries=2, retry_delay=0)
        test.addCleanup(self.monitor.stop)

    def publish(self, slug, ok=True):
        (self.sites / slug).mkdir(exist_ok=True)
        (self.sites / slug / "index.html").write_text(f"<h1>{slug}</h1>")

    def ok(self, slug):
        self.server.routes[f"/{slug}/"] = (200, f"<h1>{slug}</h1>".encode())


class SlotMapTests(unittest.TestCase):
    def test_initial_fill_unique_stable_numbering_and_extras(self):
        env = Env(self, sites=[f"site-{c}" for c in "kjihgfedcbalmnopqrstu"])   # 21 sites
        slots = env.monitor.map.load()
        self.assertEqual(len(slots), 20)
        self.assertEqual([s["slug"] for s in slots], [f"site-{c}" for c in "abcdefghijklmnopqrst"])   # ordem alfabética só no começo
        self.assertEqual(env.monitor.map.extras(), ["site-u"])
        self.assertEqual(env.monitor.map.on_removed("site-c"), 3)
        slots = env.monitor.map.load()
        self.assertIsNone(slots[2]["slug"])
        self.assertEqual(slots[3]["slug"], "site-d", "as outras posições NÃO mudam de número")
        with self.assertRaises(site_monitor.MonitorError):
            env.monitor.map.assign(3, "site-a")                            # já está na posição 1
        with self.assertRaises(site_monitor.MonitorError):
            env.monitor.map.assign(1, "site-u")                            # posição ocupada
        with self.assertRaises(site_monitor.MonitorError):
            env.monitor.map.assign(21, "site-u")
        with self.assertRaises(site_monitor.MonitorError):
            env.monitor.map.assign(3, "nao-publicado")
        with self.assertRaises(site_monitor.MonitorError):
            env.monitor.map.assign(3, "site-u", expected_path="/../x")
        env.monitor.map.assign(3, "site-u", expected_path="/index.html")
        self.assertEqual(env.monitor.map.load()[2], {"slot": 3, "slug": "site-u", "enabled": True, "expected_path": "/index.html"})
        env.publish("novo")
        self.assertIsNone(env.monitor.map.on_published("novo"), "sem vaga: fica de fora e o painel avisa")
        self.assertIn("novo", env.monitor.map.extras())

    def test_corrupted_or_duplicated_map_is_repaired_without_losing_the_file(self):
        env = Env(self, sites=["a", "b"])
        path = env.root / "monitoring" / "sites.json"
        path.parent.mkdir(parents=True)
        path.write_text("{quebrado")
        slots = env.monitor.map.load()
        self.assertEqual([s["slug"] for s in slots[:2]], ["a", "b"])
        self.assertTrue(list(path.parent.glob("sites.json.corrompido-*")), "o arquivo ruim é guardado, não apagado")
        path.write_text(json.dumps({"version": 2, "slots": [{"slot": 1, "slug": "a"}, {"slot": 2, "slug": "a"}, {"slot": 3, "slug": "../x"}, {"slot": 99}]}))
        slots = env.monitor.map.load()
        self.assertEqual([s["slug"] for s in slots[:3]], ["a", None, None])
        self.assertTrue(env.monitor.map.warnings)

    def test_map_from_3_0_keeps_numbers_and_takes_in_sites_left_out(self):
        """Atualização 3.0 → 4.0: o mapa de 10 posições vira de 20; ninguém muda de número; quem estava de fora entra."""
        sites = [f"s{n:02d}" for n in range(1, 13)]                            # 12 sites; na 3.0, s11 e s12 ficavam de fora
        env = Env(self, sites=sites)
        path = env.root / "monitoring" / "sites.json"
        path.parent.mkdir(parents=True)
        old = [{"slot": n, "slug": f"s{11 - n:02d}", "enabled": True, "expected_path": "/"} for n in range(1, 11)]   # ordem do dono
        path.write_text(json.dumps({"version": 1, "slots": old}))
        slots = env.monitor.map.load()
        self.assertEqual(len(slots), 20)
        self.assertEqual([s["slug"] for s in slots[:10]], [f"s{11 - n:02d}" for n in range(1, 11)], "posições antigas intactas")
        self.assertEqual([s["slug"] for s in slots[10:12]], ["s11", "s12"])
        self.assertEqual(env.monitor.map.extras(), [])
        self.assertIn("ampliado para 20", " ".join(env.monitor.map.warnings))
        self.assertEqual(json.loads(path.read_text())["version"], site_monitor.MAP_VERSION)
        env.monitor.map.warnings = []
        env.monitor.map.load()
        self.assertEqual(env.monitor.map.warnings, [], "a migração acontece uma vez só")


class CheckTests(unittest.TestCase):
    def test_states_are_real_and_a_failing_site_never_reads_as_vacant(self):
        env = Env(self, sites=["ok", "quebrado", "vazio", "erro", "lento", "pausado", "sumiu"])
        env.ok("ok")
        env.ok("pausado")
        env.server.routes["/vazio/"] = (200, b"")
        env.server.routes["/erro/"] = (500, b"<h1>500</h1>")
        env.server.routes["/lento/"] = "lento"
        env.pub.pause("pausado", "teste")
        env.monitor.map.load()                            # o site existia quando ganhou a posição…
        (env.sites / "sumiu" / "index.html").unlink()     # …e sumiu depois, fora do painel
        (env.sites / "sumiu").rmdir()
        report = env.monitor.run_round("manual")
        by = {s["slug"]: s for s in report["slots"] if s["slug"]}
        self.assertEqual(len(report["slots"]), 20)
        self.assertEqual(by["ok"]["state"], "ATIVO")
        self.assertEqual(by["quebrado"]["state"], "FORA_DO_AR")
        self.assertIn("404", by["quebrado"]["reason"])
        self.assertEqual(by["vazio"]["state"], "FORA_DO_AR")
        self.assertEqual(by["erro"]["state"], "FORA_DO_AR")
        self.assertEqual(by["lento"]["state"], "INCONCLUSIVO", "timeout não é 'fora do ar' confirmado")
        self.assertEqual(by["pausado"]["state"], "PAUSADO")
        self.assertEqual(by["sumiu"]["state"], "FORA_DO_AR")
        self.assertIn("pasta do site não existe", by["sumiu"]["reason"])
        self.assertNotIn("/pausado/", env.server.hits, "site pausado não recebe pedido")
        self.assertEqual([s["state"] for s in report["slots"] if not s["slug"]], ["VAGO"] * 13)
        self.assertEqual(sum(report["summary"]["counts"].values()), 20)
        self.assertEqual(report["summary"]["counts"]["VAGO"], 13)

    def test_server_down_maintenance_and_public_checks(self):
        env = Env(self, sites=["loja"], public_check=True)
        env.ok("loja")
        report = env.monitor.run_round()
        loja = report["slots"][0]
        self.assertEqual(loja["state"], "ATIVO")
        self.assertEqual(loja["public"]["result"], "nao_verificado", "sem link público conhecido não finge que testou")
        env.public_base = f"http://127.0.0.1:{env.public.port}"
        env.public.routes["/loja/"] = (530, b"tunnel error")
        self.assertEqual(env.monitor.run_round()["slots"][0]["state"], "SO_LOCAL")
        env.public.routes["/loja/"] = (200, b"<h1>loja</h1>")
        self.assertEqual(env.monitor.run_round()["slots"][0]["state"], "ATIVO")
        env.public_base = "http://nao-existe.invalid"
        state = env.monitor.run_round()["slots"][0]
        self.assertEqual(state["state"], "INCONCLUSIVO", "sem internet/DNS = inconclusivo, não 'fora do ar'")
        self.assertIn("funciona no aparelho", state["reason"])
        env.maintenance.add("loja")
        self.assertEqual(env.monitor.run_round()["slots"][0]["state"], "INCONCLUSIVO")
        env.maintenance.clear()
        env.server.close()
        down = env.monitor.run_round()["slots"][0]
        self.assertEqual(down["state"], "FORA_DO_AR")
        self.assertIn("recusada", down["reason"])

    def test_one_slow_site_does_not_block_the_others_and_rounds_never_overlap(self):
        env = Env(self, sites=[f"s{i}" for i in range(10)])
        for i in range(10):
            env.ok(f"s{i}")
        env.server.routes["/s0/"] = "lento"
        started = time.monotonic()
        report = env.monitor.run_round()
        self.assertLess(time.monotonic() - started, 6, "um site lento não segura a rodada inteira")
        self.assertEqual(report["summary"]["counts"]["ATIVO"], 9)
        env.server.routes["/s1/"] = "lento"
        thread = threading.Thread(target=env.monitor.run_round, args=("periodico",))
        thread.start()
        time.sleep(0.2)
        queued = env.monitor.request_round("manual", [3])
        self.assertFalse(queued["started"])
        self.assertTrue(queued["queued"], "o pedido não se perde: entra na fila")
        with self.assertRaises(site_monitor.MonitorError):
            env.monitor.run_round("manual", wait=False)
        thread.join(10)
        deadline = time.time() + 10
        while time.time() < deadline and not any(src == "manual-fila" for _, _, src in env.rounds):
            time.sleep(0.05)
        fila = [r for r, _, src in env.rounds if src == "manual-fila"]
        self.assertEqual(fila[0]["only"], [3], "a rodada da fila verifica só o que foi pedido")
        while env.monitor._round_lock.locked():
            time.sleep(0.05)
        self.assertTrue(env.monitor.request_round("manual")["started"])

    def test_check_single_slot_keeps_others_and_detects_changes(self):
        env = Env(self, sites=["a", "b"])
        env.ok("a")
        env.ok("b")
        env.monitor.run_round()
        env.server.routes.pop("/b/")
        report = env.monitor.run_round("manual-posicao", [2])
        self.assertEqual([s["state"] for s in report["slots"][:2]], ["ATIVO", "FORA_DO_AR"])
        _, changes, source = env.rounds[-1]
        self.assertEqual(source, "manual-posicao")
        self.assertEqual([(old["state"], new["state"]) for old, new in changes], [("ATIVO", "FORA_DO_AR")])
        history = env.monitor.history()
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]["source"], "manual-posicao")


class ScheduleTests(unittest.TestCase):
    def test_aligned_schedule_one_report_per_boundary_and_sleep_gaps(self):
        env = Env(self, sites=["a"])
        env.ok("a")
        env.clock[0] = 1_761_480_000 + 60                                   # 12:01 (relógio falso)
        self.assertEqual(env.monitor.interval_seconds(), 1800)
        self.assertIsNone(env.monitor.tick())                                # só agenda
        self.assertEqual(env.monitor.next_at, (int(env.clock[0]) // 1800 + 1) * 1800)
        env.clock[0] = env.monitor.next_at - 1
        self.assertIsNone(env.monitor.tick())
        env.clock[0] += 1
        report = env.monitor.tick()
        self.assertEqual(report["source"], "periodico")
        self.assertIsNone(env.monitor.tick(), "o mesmo horário não roda duas vezes")
        boundary = report["boundary"]
        restarted = site_monitor.Monitor(env.root, env.sites, site_port=lambda: env.server.port, publication=env.pub, maintenance=lambda s: False,
                                         settings=env.settings, public_base=lambda: "", clock=lambda: env.clock[0], retry_delay=0)
        self.addCleanup(restarted.stop)
        restarted.next_at = boundary                                          # reinício no meio do mesmo horário
        self.assertIsNone(restarted.tick(), "depois de reiniciar, o horário já enviado não é repetido")
        env.clock[0] += 5 * 1800 + 30                                        # o celular "dormiu" 5 rodadas
        burst = [env.monitor.tick() for _ in range(5)]
        self.assertEqual(sum(1 for r in burst if r), 1, "acordou: faz UMA rodada, a mais recente, não cinco")
        env.settings.data["monitor"]["enabled"] = False
        env.clock[0] += 3600
        self.assertIsNone(env.monitor.tick())
        self.assertIsNone(env.monitor.next_at)

    def test_scheduler_thread_starts_with_local_round_and_stops_cleanly(self):
        env = Env(self, sites=["a"])
        env.ok("a")
        before = threading.active_count()
        env.monitor.start(poll=0.05)
        deadline = time.time() + 5
        while time.time() < deadline and not env.rounds:
            time.sleep(0.05)
        self.assertEqual(env.rounds[0][2], "inicio")
        env.monitor.stop()
        time.sleep(0.2)
        self.assertLessEqual(threading.active_count(), before + 1, "não sobra linha de execução acumulada")


class TextTests(unittest.TestCase):
    def test_report_text_and_template_params(self):
        env = Env(self, sites=["loja-a", "catalogo"])
        env.ok("loja-a")
        report = env.monitor.run_round()
        text = site_monitor.report_text(report, next_at=1_761_483_600)
        lines = text.splitlines()
        numbered = [l for l in lines if l.split(".")[0].isdigit()]
        self.assertEqual(len(numbered), 20)
        self.assertTrue(numbered[0].startswith("1. 🔴 Fora do ar — catalogo"), numbered[0])   # ordem alfabética: catalogo, loja-a
        self.assertIn("(HTTP 404", numbered[0])
        self.assertEqual(numbered[1], "2. 🟢 Ativo — loja-a")
        self.assertEqual(numbered[2], "3. ⚪ Vago")
        self.assertIn("Resumo: 1 ativo · 1 fora do ar · 18 vagos", text)
        self.assertIn("Próxima verificação", text)
        params = site_monitor.template_params(report)
        self.assertEqual(len(params), 12)
        self.assertTrue(all("\n" not in p for p in params))
        self.assertTrue(params[1].startswith("01 🔴 Fora do ar — catalogo  |  02 🟢 Ativo — loja-a"), params[1])
        self.assertEqual(params[10], "19 ⚪ Vago  |  20 ⚪ Vago", "10 linhas de duas posições cobrem as 20")
        compact = site_monitor.compact_params(params)
        self.assertEqual(len(compact), 3)
        self.assertEqual(compact[2], "1) 🔴 Fora do ar — catalogo", "o resumido aponta só o que pede atenção")
        changes = site_monitor.changes_text([({"state": "ATIVO"}, {"slot": 2, "slug": "catalogo", "state": "FORA_DO_AR", "reason": "HTTP 404"})], report["started_at"])
        self.assertIn("2. catalogo: 🟢 Ativo → 🔴 Fora do ar (HTTP 404)", changes)


if __name__ == "__main__":
    unittest.main()
