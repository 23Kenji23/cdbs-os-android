"""3.0 / Parte 2.2, 2.5 e 2.6 — supervisor ("guardião") com processos DE VERDADE numa instalação temporária:
saúde do servidor, reinício com espera e limite (sem laço), travamento detectado, túnel (com um cloudflared de mentira),
pedidos da lista permitida, uma operação por vez, instância única, sobras de execução anterior e atualização com
verificação de saúde e volta automática. Nenhum túnel real, nenhuma internet."""
import contextlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import audit
import control
import supervisor as sup_mod
import updater
import versioninfo
from sysutil import FileLock
from test_scripts import program_files
from test_updater import package_bytes

PY = sys.executable


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def user_data(root: Path) -> dict:
    out = {}
    for rel in [".env", "data/admin.json"] + [p.relative_to(root).as_posix() for base in ("data/sites", "data/catalogs") for p in sorted((root / base).rglob("*")) if p.is_file()]:
        path = root / rel
        if path.is_file():
            out[rel] = path.read_bytes()
    return out


@contextlib.contextmanager
def installation(version="3.0.0", env_extra=""):
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder) / "cdbs"
        for rel, data in program_files().items():
            if rel.startswith("tests/") or rel.startswith("docs/"):
                continue
            target = root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            if rel.endswith(".sh"):
                target.chmod(0o755)
        (root / "VERSION.json").write_text(json.dumps(versioninfo.build_manifest(root, version, "2026-10-26")))
        ports = {"site": free_port(), "admin": free_port(), "metrics": free_port()}
        (root / ".env").write_text(f"CDBS_SITE_PORT={ports['site']}\nCDBS_ADMIN_PORT={ports['admin']}\nCDBS_TUNNEL_METRICS_PORT={ports['metrics']}\n{env_extra}")
        (root / "data" / "sites" / "adega").mkdir(parents=True)
        (root / "data" / "sites" / "adega" / "index.html").write_text("<h1>Adega</h1>")
        (root / "data" / "catalogs" / "adega").mkdir(parents=True)
        (root / "data" / "catalogs" / "adega" / "products.json").write_text('{"products": []}')
        (root / "data" / "admin.json").write_text('{"hash": "z"}')
        fake = Path(folder) / "bin" / "cloudflared"
        fake.parent.mkdir()
        fake.write_text(f"#!/bin/sh\nexec {PY} {TESTS / 'fake_cloudflared.py'} \"$@\"\n")
        fake.chmod(0o755)
        yield root, ports, fake


class Running:
    """Supervisor rodando numa linha de execução do teste, com tempos curtos."""

    def __init__(self, root, **kw):
        options = dict(health_every=0.3, health_fails=2, start_grace=8, restart_delays=(0.2,) * 7, max_crashes=4, crash_window=120,
                       tunnel_delays=(0.2,) * 6, tunnel_max=3, tunnel_lost_after=0.6, echo_server=False, poll=0.1, health_timeout=25,
                       ready_every=0.3)
        options.update(kw)
        self.sup = sup_mod.Supervisor(root, **options)
        self.code = None
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        self.code = self.sup.run()

    def wait(self, predicate, timeout=30, what="condição"):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if predicate():
                return True
            time.sleep(0.1)
        raise AssertionError(f"tempo esgotado esperando: {what} (servidor={self.sup.state['server']}, túnel={self.sup.state['tunnel']})")

    def healthy(self):
        return self.sup.state["server"]["state"] == "rodando"

    def request(self, op, args=None, timeout=60):
        rid = control.submit(self.sup.data, op, args, by="teste")
        result = control.wait(self.sup.data, rid, timeout)
        assert result is not None, f"sem resultado para {op}"
        return result

    def stop(self):
        self.sup.request_stop()
        self.thread.join(30)


def actions(root):
    return [(e["action"], e["result"]) for e in reversed(audit.AuditLog(root / "data" / "system").query(limit=500))]


class ServerLifecycleTests(unittest.TestCase):
    def test_starts_server_answers_requests_and_stops_everything(self):
        with installation() as (root, ports, _fake):
            run = Running(root)
            try:
                run.wait(run.healthy, what="servidor saudável")
                info = json.loads((root / "data" / "system" / "supervisor.json").read_text())
                self.assertEqual(info["pid"], os.getpid())
                self.assertTrue(control.supervisor_alive(root / "data"))
                child_pid = run.sup.server.pid
                result = run.request("server_restart")
                self.assertEqual(result["status"], "ok", result)
                self.assertNotEqual(run.sup.server.pid, child_pid, "reiniciou de verdade (outro processo)")
                requests_dir = root / "data" / "system" / "control" / "requests"
                (requests_dir / "aaaaaaaaaaaaaaaa.json").write_text(json.dumps({"id": "aaaaaaaaaaaaaaaa", "op": "rm_rf", "created_at": time.time()}))
                (requests_dir / "bbbbbbbbbbbbbbbb.json").write_text(json.dumps({"id": "bbbbbbbbbbbbbbbb", "op": "server_restart", "created_at": time.time() - 3600}))
                run.wait(lambda: control.result(root / "data", "bbbbbbbbbbbbbbbb") is not None, what="pedidos processados")
                self.assertEqual(control.result(root / "data", "aaaaaaaaaaaaaaaa")["status"], "recusado")
                self.assertEqual(control.result(root / "data", "bbbbbbbbbbbbbbbb")["status"], "expirado")
                with self.assertRaises(control.ControlError):
                    control.submit(root / "data", "shell", {"cmd": "rm -rf /"})
                child = run.sup.server.proc
            finally:
                run.stop()
            self.assertEqual(run.code, 0)
            self.assertIsNotNone(child.poll(), "ao parar o supervisor, o servidor também para")
            info = json.loads((root / "data" / "system" / "supervisor.json").read_text())
            self.assertTrue(info.get("stopped_at"))
            self.assertIsNone(control.supervisor_alive(root / "data"))
            self.assertIn(("supervisor_parado", "ok"), actions(root))

    def test_crash_loop_backs_off_and_then_gives_up(self):
        with installation() as (root, _ports, _fake):
            run = Running(root, server_argv=[PY, "-c", "import sys; print('quebrou de proposito'); sys.exit(1)"])
            try:
                run.wait(lambda: run.sup.state["server"]["state"] == "parado_por_falhas", timeout=40, what="desistir depois das falhas")
                restarts = [a for a in actions(root) if a[0] == "servidor_reiniciado"]
                self.assertEqual(len(restarts), 3, "tentou com espera e parou no limite (4 falhas)")
                self.assertIn(("servidor_parado_por_falhas", "falha"), actions(root))
                self.assertIn("quebrou de proposito", run.sup.state["server"]["message"])
                time.sleep(1.5)
                self.assertEqual(len([a for a in actions(root) if a[0] == "servidor_reiniciado"]), 3, "depois de desistir, não tenta mais sozinho")
            finally:
                run.stop()

    def test_cannot_start_is_reported_with_the_reason(self):
        with installation() as (root, _ports, _fake):
            script = "import sys; print('CDBS.OS não iniciou: a porta 8088 (sites) já está em uso.', file=sys.stderr); sys.exit(3)"
            run = Running(root, server_argv=[PY, "-c", script], max_crashes=2)
            try:
                run.wait(lambda: run.sup.state["server"]["state"] == "parado_por_falhas", timeout=30, what="parar")
                failed = audit.AuditLog(root / "data" / "system").query(category="servico", limit=50)
                reasons = [e["detail"] for e in failed if e["action"] == "inicio_falhou"]
                self.assertTrue(reasons and "já está em uso" in reasons[0], failed)
            finally:
                run.stop()

    def test_hung_server_is_detected_and_restarted(self):
        with installation() as (root, ports, _fake):
            hang = ("import socket, time\n"
                    f"a = socket.socket(); a.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); a.bind(('127.0.0.1', {ports['admin']})); a.listen()\n"
                    f"b = socket.socket(); b.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); b.bind(('127.0.0.1', {ports['site']})); b.listen()\n"
                    "time.sleep(600)\n")
            run = Running(root, server_argv=[PY, "-c", hang], start_grace=0.5, max_crashes=10)
            try:
                run.wait(lambda: ("servidor_travado", "falha") in actions(root), timeout=40, what="detectar travamento")
                run.wait(lambda: run.sup.server is not None and run.sup.server.alive(), timeout=10, what="reiniciar")
            finally:
                run.stop()

    def test_single_instance_and_orphan_cleanup(self):
        with installation() as (root, _ports, _fake):
            holder = FileLock(root / "data" / "system" / "supervisor.lock")
            self.assertTrue(holder.acquire(owner="outro supervisor"))
            try:
                second = sup_mod.Supervisor(root, echo_server=False)
                self.assertEqual(second.run(), sup_mod.EXIT_CANNOT_START)
            finally:
                holder.release()
            orphan = subprocess.Popen([PY, "-c", "import time; time.sleep(120)", "cdbs.py", "serve"])
            try:
                (root / "data" / "system" / "supervisor.json").write_text(json.dumps({"pid": 999999, "server": {"pid": orphan.pid}, "tunnel": {"pid": None}}))
                sup_mod.Supervisor(root, echo_server=False)._cleanup_orphans()
                orphan.wait(10)
                self.assertIsNotNone(orphan.returncode, "o servidor que sobrou da execução anterior foi encerrado")
            finally:
                if orphan.poll() is None:
                    orphan.kill()
            innocent = subprocess.Popen([PY, "-c", "import time; time.sleep(120)"])
            try:
                (root / "data" / "system" / "supervisor.json").write_text(json.dumps({"server": {"pid": innocent.pid}}))
                sup_mod.Supervisor(root, echo_server=False)._cleanup_orphans()
                time.sleep(0.3)
                self.assertIsNone(innocent.poll(), "processo que não é do CDBS nunca é encerrado")
            finally:
                innocent.kill()
                innocent.wait()


class TunnelTests(unittest.TestCase):
    def test_start_captures_link_points_only_to_site_port_and_stops(self):
        with installation() as (root, ports, fake), tempfile.TemporaryDirectory() as tmp:
            args_file = Path(tmp) / "args.jsonl"
            with patch.dict(os.environ, {"FAKE_CF_URL": "https://abc-def-ghi.trycloudflare.com", "FAKE_CF_ARGS_FILE": str(args_file)}):
                run = Running(root, cloudflared=str(fake))
                try:
                    run.wait(run.healthy, what="servidor")
                    result = run.request("tunnel_start")
                    self.assertEqual(result["status"], "ok", result)
                    self.assertEqual(result["url"], "https://abc-def-ghi.trycloudflare.com")
                    tunnel = json.loads((root / "data" / "system" / "tunnel.json").read_text())
                    self.assertEqual((tunnel["state"], tunnel["url"]), ("conectado", "https://abc-def-ghi.trycloudflare.com"))
                    recorded = json.loads(args_file.read_text().splitlines()[0])
                    self.assertIn(f"http://127.0.0.1:{ports['site']}", recorded["args"])
                    self.assertNotIn(str(ports["admin"]), " ".join(recorded["args"]), "o painel (porta admin) NUNCA vai para o túnel")
                    self.assertIn(("tunel_link_novo", "info"), actions(root))
                    again = run.request("tunnel_start")
                    self.assertIn("já estava ligado", again["message"])
                    stopped = run.request("tunnel_stop")
                    self.assertEqual(stopped["status"], "ok")
                    self.assertEqual(json.loads((root / "data" / "system" / "tunnel.json").read_text())["state"], "desligado")
                finally:
                    run.stop()

    def test_crash_restart_limit_and_lost_connection(self):
        with installation() as (root, _ports, fake), tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"FAKE_CF_EXIT_AFTER": "1"}):
                run = Running(root, cloudflared=str(fake))
                try:
                    run.wait(run.healthy, what="servidor")
                    run.request("tunnel_start", {"wait": 5})
                    run.wait(lambda: run.sup.state["tunnel"]["state"] == "parado_por_falhas", timeout=30, what="túnel desistir")
                    self.assertGreaterEqual(len([a for a in actions(root) if a[0] == "tunel_caiu"]), 2)
                    self.assertIn(("tunel_parado_por_falhas", "falha"), actions(root))
                finally:
                    run.stop()
            ready = Path(tmp) / "ready"
            with patch.dict(os.environ, {"FAKE_CF_READY_FILE": str(ready)}):
                run = Running(root, cloudflared=str(fake))
                try:
                    run.wait(run.healthy, what="servidor")
                    self.assertEqual(run.request("tunnel_start")["status"], "ok")
                    ready.write_text("0")                                             # internet caiu
                    run.wait(lambda: run.sup.state["tunnel"]["state"] == "desconectado", timeout=15, what="perceber a queda")
                    ready.write_text("1")                                             # voltou
                    run.wait(lambda: run.sup.state["tunnel"]["state"] == "conectado", timeout=15, what="perceber a volta")
                    self.assertTrue(run.sup.tunnel.alive(), "queda de rede não reinicia o cloudflared em laço")
                finally:
                    run.stop()

    def test_missing_binary_named_without_token_and_off(self):
        with installation() as (root, _ports, fake):
            run = Running(root, cloudflared=str(root / "nao-existe"))
            try:
                run.wait(run.healthy, what="servidor")
                result = run.request("tunnel_start")
                self.assertEqual(result["status"], "falhou")
                self.assertIn("pkg install cloudflared", result["message"])
            finally:
                run.stop()
        with installation(env_extra="CDBS_TUNNEL_MODE=named\n") as (root, _ports, fake):
            run = Running(root, cloudflared=str(fake))
            try:
                run.wait(run.healthy, what="servidor")
                result = run.request("tunnel_start")
                self.assertEqual(result["status"], "falhou")
                self.assertIn("token", result["message"])
            finally:
                run.stop()


class UpdateThroughSupervisorTests(unittest.TestCase):
    def install_package(self, run, root, data, allow=False):
        report = updater.Updater(root, root / "data").stage_upload(__import__("io").BytesIO(data), len(data))
        self.assertTrue(report["ok"], report["problems"])
        return run.request("update_apply", {"staged_id": report["id"], "allow_same_or_older": allow}, timeout=120)

    def test_good_update_is_installed_and_verified(self):
        with installation() as (root, _ports, _fake):
            before = user_data(root)
            run = Running(root)
            try:
                run.wait(run.healthy, what="servidor")
                result = self.install_package(run, root, package_bytes(version="3.0.1"))
                self.assertEqual(result["status"], "ok", result)
                self.assertEqual(result["version"], "3.0.1")
                self.assertEqual(versioninfo.load(root)["version"], "3.0.1")
                self.assertTrue(run.healthy())
                self.assertEqual(user_data(root), before, ".env, sites, catálogos e senha ficam idênticos")
                self.assertEqual(updater.Updater(root, root / "data").journal()["step"], "CONCLUIDO")
                self.assertIn(("atualizacao_concluida", "ok"), actions(root))
                self.assertEqual(updater.Updater(root, root / "data").snapshots()[0]["version"], "3.0.0")
            finally:
                run.stop()

    def test_broken_update_rolls_back_automatically(self):
        with installation() as (root, _ports, _fake):
            before = user_data(root)
            old_cdbs = (root / "cdbs.py").read_bytes()
            files = program_files()
            files["cdbs.py"] = b"raise SystemExit('quebrado de proposito: a versao nova nao sobe')\n"
            run = Running(root)
            try:
                run.wait(run.healthy, what="servidor")
                result = self.install_package(run, root, package_bytes(version="3.0.1", files=files))
                self.assertEqual(result["status"], "falhou", result)
                self.assertTrue(result.get("reverted"))
                self.assertIn("versão anterior 3.0.0 foi recolocada", result["message"])
                self.assertEqual(versioninfo.load(root)["version"], "3.0.0")
                self.assertEqual((root / "cdbs.py").read_bytes(), old_cdbs)
                run.wait(run.healthy, what="versão antiga de volta no ar")
                self.assertEqual(user_data(root), before)
                self.assertIn(("atualizacao_revertida", "falha"), actions(root))
            finally:
                run.stop()

    def test_new_supervisor_failing_selftest_also_rolls_back(self):
        with installation() as (root, _ports, _fake):
            files = program_files()
            files["supervisor.py"] = files["supervisor.py"].replace(b"def _selftest() -> int:\n", b"def _selftest() -> int:\n    raise SystemExit(7)\n", 1)
            run = Running(root)
            try:
                run.wait(run.healthy, what="servidor")
                result = self.install_package(run, root, package_bytes(version="3.0.1", files=files))
                self.assertEqual(result["status"], "falhou", result)
                self.assertIn("autoteste", result["message"])
                self.assertEqual(versioninfo.load(root)["version"], "3.0.0")
            finally:
                run.stop()

    def test_one_update_at_a_time_and_rollback_request(self):
        with installation() as (root, _ports, _fake):
            run = Running(root)
            try:
                run.wait(run.healthy, what="servidor")
                lock = updater.Updater(root, root / "data").lock()
                self.assertTrue(lock.acquire(owner="outra atualização"))
                try:
                    data = package_bytes(version="3.0.1")
                    report = updater.Updater(root, root / "data").stage_upload(__import__("io").BytesIO(data), len(data))
                    busy = run.request("update_apply", {"staged_id": report["id"]})
                    self.assertEqual(busy["status"], "recusado")
                finally:
                    lock.release()
                self.assertEqual(run.request("update_apply", {"staged_id": report["id"]}, timeout=120)["status"], "ok")
                snap = updater.Updater(root, root / "data").snapshots()[0]
                back = run.request("update_rollback", {"snapshot_id": snap["id"]}, timeout=120)
                self.assertEqual(back["status"], "ok", back)
                self.assertEqual(versioninfo.load(root)["version"], "3.0.0")
            finally:
                run.stop()

    def test_interrupted_update_is_reverted_before_the_server_starts(self):
        with installation() as (root, _ports, _fake):
            old_cdbs = (root / "cdbs.py").read_bytes()
            upd = updater.Updater(root, root / "data")
            files = program_files()
            files["cdbs.py"] = files["cdbs.py"] + b"\n# nova\n"
            data = package_bytes(version="3.0.1", files=files)
            report = upd.stage_upload(__import__("io").BytesIO(data), len(data))
            journal = upd.begin(staged_id=report["id"])
            upd.make_snapshot(journal)
            upd._set_step(journal, "INSTALANDO")
            (root / "cdbs.py").write_bytes(files["cdbs.py"])                         # metade copiada quando o celular desligou
            run = Running(root)
            try:
                run.wait(run.healthy, what="servidor")
                self.assertEqual((root / "cdbs.py").read_bytes(), old_cdbs)
                self.assertEqual(updater.Updater(root, root / "data").journal()["step"], "REVERTIDO")
                self.assertIn(("atualizacao_revertida", "falha"), actions(root))
            finally:
                run.stop()


if __name__ == "__main__":
    unittest.main()
