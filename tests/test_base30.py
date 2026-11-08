"""3.0 / Parte 1 — base: utilidades, .env, manifesto de versão, histórico de auditoria e sinal de vida. Sem rede."""
import json
import multiprocessing
import os
import stat
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import audit
import config
import lifecycle
import sysutil
import versioninfo


def _append_many(folder: str, n: int) -> None:
    log = audit.AuditLog(Path(folder))
    for i in range(n):
        log.record("sistema", "teste_paralelo", "ok", detail=f"linha {i}")


class SysutilTests(unittest.TestCase):
    def test_json_is_atomic_private_and_tolerant(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "a" / "b.json"
            sysutil.write_json_private(path, {"x": 1})
            self.assertEqual(sysutil.read_json(path), {"x": 1})
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
            path.write_text("{cortado", encoding="utf-8")
            self.assertEqual(sysutil.read_json(path), {})
            self.assertEqual(sysutil.read_json(Path(folder) / "nao-existe.json", []), [])
            self.assertEqual([p.name for p in path.parent.iterdir() if p.name.endswith(".tmp")], [])

    def test_masking(self):
        self.assertEqual(sysutil.mask_phone("+55 (85) 99999-8888"), "+55•••••••8888")
        self.assertEqual(sysutil.mask_phone("12"), "•••")
        self.assertEqual(sysutil.mask_secret("EAAGabcdefghijklmnop1234"), "••••1234")
        self.assertEqual(sysutil.mask_secret("curto"), "••••")
        self.assertEqual(sysutil.mask_secret(""), "")
        token = "EAAG" + "Zx9" * 20
        text = sysutil.scrub(f"erro com token={token} para 5585999998888 cookie cdbs_session=abc.def em 2026-10-26 Bearer {token}")
        self.assertNotIn(token, text)
        self.assertNotIn("5585999998888", text)
        self.assertNotIn("abc.def", text)
        self.assertIn("2026-10-26", text)                     # datas não são confundidas com telefone
        self.assertIn("8888", text)                           # o final do número continua reconhecível
        self.assertEqual(sysutil.scrub("linha1\nlinha2"), "linha1 linha2")
        self.assertIn("francis-kept-lists-basin.trycloudflare.com", sysutil.scrub("link https://francis-kept-lists-basin.trycloudflare.com"))

    def test_file_lock_is_exclusive_and_released(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "x.lock"
            first, second = sysutil.FileLock(path), sysutil.FileLock(path)
            self.assertTrue(first.acquire(owner="atualizacao"))
            self.assertFalse(second.acquire(timeout=0.2))
            self.assertEqual(first.info()["owner"], "atualizacao")
            first.release()
            self.assertTrue(second.acquire(timeout=0.2))
            second.release()

    def test_file_lock_released_when_holder_process_dies(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "x.lock"
            ctx = multiprocessing.get_context("fork")
            ready = ctx.Event()

            def hold() -> None:
                lock = sysutil.FileLock(path)
                lock.acquire()
                ready.set()
                time.sleep(30)

            proc = ctx.Process(target=hold)
            proc.start()
            self.assertTrue(ready.wait(10))
            self.assertFalse(sysutil.FileLock(path).acquire(timeout=0.2))
            proc.kill()
            proc.join(5)
            again = sysutil.FileLock(path)
            self.assertTrue(again.acquire(timeout=2))      # morreu → o sistema soltou a trava
            again.release()


class ConfigTests(unittest.TestCase):
    def test_parse_env_known_unknown_quotes_and_export(self):
        with tempfile.TemporaryDirectory() as folder:
            env = Path(folder) / ".env"
            env.write_text("# comentário\nCDBS_SITE_PORT=9000\nexport CDBS_ADMIN_PORT='9001'\nCDBS_PUBLIC_BASE=\"https://x.com\"\n"
                           "FOO=1\nlinha-sem-igual\nCDBS_WHATSAPP_ACCESS_TOKEN=segredo\n", encoding="utf-8")
            values, warnings = config.parse_env(env)
            self.assertEqual(values["CDBS_SITE_PORT"], "9000")
            self.assertEqual(values["CDBS_ADMIN_PORT"], "9001")
            self.assertEqual(values["CDBS_PUBLIC_BASE"], "https://x.com")
            self.assertNotIn("FOO", values)
            self.assertEqual(len(warnings), 2)
            self.assertTrue(all("segredo" not in w for w in warnings))
            self.assertEqual(config.parse_env(Path(folder) / "nada"), ({}, []))


class VersionInfoTests(unittest.TestCase):
    def test_manifest_build_verify_and_tamper_detection(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "web").mkdir()
            (root / "data" / "sites").mkdir(parents=True)
            (root / "cdbs.py").write_text("print(1)\n")
            (root / "web" / "app.js").write_text("x\n")
            (root / ".env").write_text("CDBS_SITE_PORT=1\n")
            (root / "data" / "admin.json").write_text("{}")
            (root / "data" / "sites" / ".keep").write_text("\n")
            (root / "__pycache__").mkdir()
            (root / "__pycache__" / "x.pyc").write_bytes(b"0")
            manifest = versioninfo.build_manifest(root, "3.0.0", "2026-10-26")
            self.assertEqual(sorted(manifest["files"]), ["cdbs.py", "data/sites/.keep", "web/app.js"])
            (root / versioninfo.MANIFEST).write_text(json.dumps(manifest))
            info = versioninfo.load(root)
            self.assertTrue(info["valid"])
            self.assertTrue(versioninfo.verify(root, info)["ok"])
            (root / "web" / "app.js").write_text("alterado\n")
            (root / "cdbs.py").unlink()
            result = versioninfo.verify(root, info)
            self.assertEqual(result["missing"], ["cdbs.py"])
            self.assertEqual(result["changed"], ["web/app.js"])
            self.assertEqual(versioninfo.version_tuple("3.10.2"), (3, 10, 2))
            self.assertEqual(versioninfo.version_tuple("x"), (0, 0, 0))
            (root / versioninfo.MANIFEST).write_text("{quebrado")
            self.assertFalse(versioninfo.load(root)["valid"])


class AuditTests(unittest.TestCase):
    def test_record_query_filters_and_scrub(self):
        with tempfile.TemporaryDirectory() as folder:
            clock = [1_000_000.0]
            log = audit.AuditLog(Path(folder), clock=lambda: clock[0])
            token = "EAAG" + "q1" * 30
            log.record("site", "site_publicado", "ok", actor="painel", target="adega", detail="12 bytes")
            clock[0] += 10
            log.record("bot", "comando_recusado", "recusado", actor="+55 85 99999-8888", detail=f"token={token}")
            clock[0] += 10
            log.record("categoria-que-nao-existe", "x", "resultado-estranho")
            items = log.query()
            self.assertEqual([i["action"] for i in items], ["x", "comando_recusado", "site_publicado"])   # mais recentes primeiro
            self.assertEqual(items[0]["category"], "sistema")
            self.assertEqual(items[0]["result"], "info")
            self.assertEqual([i["action"] for i in log.query(category="site")], ["site_publicado"])
            self.assertEqual([i["action"] for i in log.query(result="recusado")], ["comando_recusado"])
            self.assertEqual([i["action"] for i in log.query(text="ADEGA")], ["site_publicado"])
            self.assertEqual([i["action"] for i in log.query(since=1_000_015)], ["x"])
            raw = (Path(folder) / "audit.jsonl").read_text()
            self.assertNotIn(token, raw)
            self.assertNotIn("99999-8888", raw)
            self.assertEqual(stat.S_IMODE((Path(folder) / "audit.jsonl").stat().st_mode), 0o600)
            self.assertEqual(log.last("site")["target"], "adega")

    def test_rotation_keeps_bounded_files_and_query_spans_them(self):
        with tempfile.TemporaryDirectory() as folder:
            log = audit.AuditLog(Path(folder), max_bytes=2000, keep=3)
            for i in range(200):
                log.record("sistema", "rotacao", "ok", detail=f"evento {i:04d} " + "x" * 40)
            files = sorted(p.name for p in Path(folder).glob("audit*.jsonl"))
            self.assertEqual(files, ["audit.1.jsonl", "audit.2.jsonl", "audit.jsonl"])
            self.assertTrue(all(p.stat().st_size <= 2000 for p in Path(folder).glob("audit*.jsonl")))
            newest = log.query(limit=1)[0]
            self.assertIn("evento 0199", newest["detail"])
            kept = log.query(limit=500)
            self.assertGreaterEqual(len(kept), 15)
            self.assertLess(len(kept), 200, "o histórico é limitado: os mais antigos saem")
            numbers = [int(e["detail"].split()[1]) for e in kept]
            self.assertEqual(numbers, list(range(199, 199 - len(kept), -1)), "o que fica é contínuo e o mais recente")

    def test_listener_errors_and_corrupt_lines_do_not_break(self):
        with tempfile.TemporaryDirectory() as folder:
            log = audit.AuditLog(Path(folder))
            seen = []
            log.add_listener(lambda e: (_ for _ in ()).throw(RuntimeError("ouvinte quebrado")))
            log.add_listener(seen.append)
            log.record("sistema", "a")
            with open(Path(folder) / "audit.jsonl", "a") as handle:
                handle.write('{"meia linha cortada pelo desligamento\n')
            log.record("sistema", "b")
            self.assertEqual([e["action"] for e in seen], ["a", "b"])
            self.assertEqual([e["action"] for e in log.query()], ["b", "a"])

    def test_two_processes_append_without_losing_lines(self):
        with tempfile.TemporaryDirectory() as folder:
            ctx = multiprocessing.get_context("fork")
            procs = [ctx.Process(target=_append_many, args=(folder, 100)) for _ in range(3)]
            for p in procs:
                p.start()
            for p in procs:
                p.join(30)
            lines = (Path(folder) / "audit.jsonl").read_text().splitlines()
            self.assertEqual(len(lines), 300)
            self.assertTrue(all(json.loads(line)["action"] == "teste_paralelo" for line in lines))


class HeartbeatTests(unittest.TestCase):
    def test_unclean_previous_run_is_detected_and_clean_stop_is_not(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "system" / "x-heartbeat.json"
            clock = [1_700_000_000.0]
            beat = lifecycle.Heartbeat(path, interval=0.05, clock=lambda: clock[0])
            self.assertIsNone(beat.previous())
            beat.start(version="3.0.0")
            clock[0] += 120
            time.sleep(0.2)                                     # o laço regrava o sinal de vida
            data = json.loads(path.read_text())
            self.assertFalse(data["clean"])
            self.assertEqual(data["beat_at"], 1_700_000_120)
            # simula o Android matando o processo: ninguém chama stop()
            beat._stop.set()
            prev = lifecycle.Heartbeat(path).previous()
            self.assertIsNotNone(prev)
            self.assertEqual(prev["last_beat_at"], 1_700_000_120)
            self.assertIn("Último sinal de vida", prev["message"])
            again = lifecycle.Heartbeat(path, interval=60)
            again.start()
            again.stop(clean=True)
            self.assertIsNone(lifecycle.Heartbeat(path).previous())
            self.assertTrue(json.loads(path.read_text())["clean"])


if __name__ == "__main__":
    unittest.main()
