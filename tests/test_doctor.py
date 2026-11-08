"""3.0 / Parte 1.5 — diagnóstico `python3 cdbs.py doctor`: só lê por padrão, não vaza segredos, explica e sugere."""
import contextlib
import json
import os
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import doctor
import versioninfo

FAKE_TOKEN = "EAAGtokenFALSOdeTESTE1234567890abcdefXYZ"


def checks(report, area=None, name=None):
    return [c for c in report["checks"] if (area is None or c["area"] == area) and (name is None or c["name"] == name)]


@contextlib.contextmanager
def fake_root(env_text=""):
    """Cópia mínima de uma instalação: só o necessário para o diagnóstico."""
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder) / "cdbs os"                      # com espaço no caminho, de propósito
        root.mkdir()
        for name in ("cdbs.py", "doctor.py", "config.py", "versioninfo.py", "sysutil.py"):
            shutil.copy2(ROOT / name, root / name)
        if env_text:
            (root / ".env").write_text(env_text, encoding="utf-8")
        yield root


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class DoctorTests(unittest.TestCase):
    def test_fresh_install_is_read_only_and_explains(self):
        with fake_root() as root:
            report = doctor.run(root, offline=True)
            self.assertFalse((root / "data").exists(), "o diagnóstico NÃO pode criar nada sem --corrigir")
            self.assertEqual(checks(report, "dados", "pasta de dados")[0]["status"], "AVISO")
            self.assertEqual(checks(report, "internet")[0]["status"], "INFO")
            ports = checks(report, "configuração", "portas")[0]
            self.assertEqual(ports["status"], "OK")
            self.assertIn("8088", ports["message"])
            text = doctor.render_text(report)
            self.assertIn("resumo:", text)
            self.assertIn("não contém senhas", text)

    def test_never_prints_secrets_and_flags_bad_config(self):
        env = (f"CDBS_SITE_PORT=80\nCDBS_ADMIN_PORT=8089\nCDBS_PUBLIC_BASE=http://inseguro.example\n"
               f"CDBS_WHATSAPP_ACCESS_TOKEN={FAKE_TOKEN}\nCHAVE_ESTRANHA=1\n")
        with fake_root(env) as root:
            os.chmod(root / ".env", 0o644)
            report = doctor.run(root, offline=True)
            dump = json.dumps(report) + doctor.render_text(report)
            self.assertNotIn(FAKE_TOKEN, dump)
            self.assertNotIn(FAKE_TOKEN[-12:], dump)
            self.assertEqual(checks(report, "configuração", "portas")[0]["status"], "FALHA")
            self.assertEqual(checks(report, "configuração", "endereço público")[0]["status"], "FALHA")
            self.assertTrue(any("CHAVE_ESTRANHA" in c["message"] for c in checks(report, "configuração", ".env")))
            self.assertEqual(checks(report, "configuração", "permissão .env")[0]["status"], "AVISO")
            self.assertEqual(stat.S_IMODE((root / ".env").stat().st_mode), 0o644, "sem --corrigir nada muda")
            fixed = doctor.run(root, offline=True, fix=True)
            self.assertEqual(stat.S_IMODE((root / ".env").stat().st_mode), 0o600)
            self.assertIn("permissão do .env ajustada para 600", fixed["fixes"])

    def test_same_ports_and_loose_private_files(self):
        with fake_root("CDBS_SITE_PORT=8089\n") as root:
            data = root / "data"
            (data / "sites" / "adega").mkdir(parents=True)
            (data / "sites" / "quebrado").mkdir()
            (data / "sites" / "adega" / "index.html").write_text("<h1>ok</h1>")
            (data / "admin.json").write_text("{}")
            os.chmod(data / "admin.json", 0o644)
            (data / "backups").mkdir()
            os.chmod(data / "backups", 0o755)
            report = doctor.run(root, offline=True)
            self.assertEqual(checks(report, "configuração", "portas")[0]["status"], "FALHA")
            perms = checks(report, "dados", "permissões")[0]
            self.assertEqual(perms["status"], "AVISO")
            self.assertIn("admin.json", perms["message"])
            sites = checks(report, "dados", "sites")[0]
            self.assertIn("quebrado", sites["message"])
            self.assertEqual(checks(report, "backups", "senha de recuperação")[0]["status"], "AVISO")
            doctor.run(root, offline=True, fix=True)
            self.assertEqual(stat.S_IMODE((data / "admin.json").stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE((data / "backups").stat().st_mode), 0o700)

    def test_unclean_shutdown_is_reported_with_last_sign_of_life(self):
        with fake_root() as root:
            (root / "data" / "system").mkdir(parents=True)
            (root / "data" / "system" / "cdbs-heartbeat.json").write_text(json.dumps(
                {"pid": 1, "started_at": 1_761_480_000, "beat_at": 1_761_483_600, "clean": False}))
            report = doctor.run(root, offline=True)
            item = checks(report, "processos", "último encerramento")[0]
            self.assertEqual(item["status"], "AVISO")
            self.assertIn("SEM desligar normalmente", item["message"])
            self.assertIn("termux-wake-lock", item["action"])

    def test_3_0_states_are_explained_only_read_and_never_show_secrets(self):
        import settings as settings_mod
        with fake_root() as root:
            data = root / "data"
            (data / "sites" / "adega").mkdir(parents=True)
            (data / "sites" / "adega" / "index.html").write_text("<h1>adega</h1>")
            # 10 posições: a 2 aponta para um site cuja pasta sumiu; a última verificação é de 3 h atrás
            slots = [{"slot": n, "slug": None, "enabled": True, "expected_path": "/"} for n in range(1, 11)]
            slots[0]["slug"], slots[1]["slug"] = "adega", "sumiu"
            (data / "monitoring").mkdir()
            (data / "monitoring" / "sites.json").write_text(json.dumps({"slots": slots}))
            (data / "monitoring" / "last_report.json").write_text(json.dumps({"started_at": time.time() - 3 * 3600, "summary": {"text": "1 ativo"}}))
            # o último envio de notificação falhou
            (data / "notifications").mkdir()
            (data / "notifications" / "events.json").write_text(json.dumps({"events": [
                {"title": "Teste", "status": "falhou", "deliveries": [{"error": "TOKEN_INVALIDO: o token expirou"}]}]}))
            # WhatsApp configurado; bot ligado SEM a chave do app
            config_ = settings_mod.Settings(data / "system", environ={})
            config_.set_secret("whatsapp_phone_number_id", "123456789")
            config_.set_secret("whatsapp_token", FAKE_TOKEN)
            config_.update("notifications", {"enabled": True, "recipients": ["+55 85 99999-8888"]})
            config_.update("bot", {"enabled": True, "admins": ["+55 85 99999-8888"]})
            # o supervisor não terminou normalmente (o processo registrado não existe mais)
            gone = subprocess.Popen([sys.executable, "-c", "pass"])
            gone.wait()
            (data / "system" / "supervisor.json").write_text(json.dumps({"pid": gone.pid, "heartbeat_at": time.time() - 600}))
            # atualização interrompida no meio da instalação
            (data / "updates").mkdir()
            journal = {"id": "j1", "step": "INSTALANDO", "from_version": "3.0.0", "to_version": "3.0.1"}
            (data / "updates" / "journal.json").write_text(json.dumps(journal))
            # um site removido guardado
            kept = data / "removidos" / "sites" / "0123456789ab" / "site"
            kept.mkdir(parents=True)
            (kept / "index.html").write_text("x" * 2048)
            before = sorted((str(p), p.stat().st_mtime_ns) for p in data.rglob("*"))
            report = doctor.run(root, offline=True)
            self.assertEqual(sorted((str(p), p.stat().st_mtime_ns) for p in data.rglob("*")), before, "sem --corrigir o diagnóstico só lê")
            by = {(c["area"], c["name"]): c for c in report["checks"]}
            self.assertEqual(by[("monitoramento", "posições")]["status"], "AVISO")
            self.assertIn("sem pasta: sumiu", by[("monitoramento", "posições")]["message"])
            self.assertEqual(by[("monitoramento", "última verificação")]["status"], "AVISO")
            self.assertEqual(by[("notificações", "envios com problema")]["status"], "AVISO")
            self.assertIn("TOKEN_INVALIDO", by[("notificações", "envios com problema")]["message"])
            self.assertEqual(by[("whatsapp", "envio")]["status"], "OK")
            self.assertEqual(by[("whatsapp", "bot")]["status"], "FALHA")
            self.assertIn("App Secret", by[("whatsapp", "bot")]["message"])
            self.assertEqual(by[("supervisor", "supervisor")]["status"], "AVISO")
            self.assertIn("NÃO terminou normalmente", by[("supervisor", "supervisor")]["message"])
            self.assertEqual(by[("atualizações", "atualização em andamento")]["status"], "AVISO")
            self.assertEqual(by[("dados", "sites removidos (guardados)")]["status"], "INFO")
            everything = doctor.render_text(report) + json.dumps(report, ensure_ascii=False)
            for secret in (FAKE_TOKEN, "99999-8888", "5585999998888"):
                self.assertNotIn(secret, everything)
            # a volta automática também falhou: FALHA com o caminho de recuperação manual
            journal.update(step="FALHOU_REVERSAO", message="interrompida durante a instalação e a volta falhou")
            (data / "updates" / "journal.json").write_text(json.dumps(journal))
            item = checks(doctor.run(root, offline=True), "atualizações", "última atualização")[0]
            self.assertEqual(item["status"], "FALHA")
            self.assertIn("python3 updater.py voltar", item["action"])

    def test_program_integrity_against_manifest(self):
        with fake_root() as root:
            manifest = versioninfo.build_manifest(root, "3.0.0", "2026-10-26")
            (root / "VERSION.json").write_text(json.dumps(manifest))
            ok = doctor.run(root, offline=True)
            self.assertEqual(checks(ok, "programa", "integridade")[0]["status"], "OK")
            (root / "sysutil.py").write_text("# mexido\n")
            bad = doctor.run(root, offline=True)
            item = checks(bad, "programa", "integridade")[0]
            self.assertEqual(item["status"], "AVISO")
            self.assertIn("sysutil.py", item["message"])

    def test_missing_cryptography_is_a_clear_failure(self):
        with fake_root() as root, patch.dict(sys.modules, {"cryptography": None}):
            report = doctor.run(root, offline=True)
            item = checks(report, "ambiente", "criptografia")[0]
            self.assertEqual(item["status"], "FALHA")
            self.assertIn("pkg install python-cryptography", item["action"])

    def test_detects_running_server_through_health_route(self):
        import cdbs
        from test_catalog import stack
        with stack() as urls:
            admin_port = int(urls["admin"].rsplit(":", 1)[1])
            site_port = int(urls["public"].rsplit(":", 1)[1])
            with fake_root(f"CDBS_SITE_PORT={site_port}\nCDBS_ADMIN_PORT={admin_port}\n") as root:
                report = doctor.run(root, offline=True)
                admin = checks(report, "processos", "painel (8089)")[0]
                self.assertEqual(admin["status"], "OK", admin)
                self.assertIn(cdbs.APP_VERSION, admin["message"])
                self.assertEqual(checks(report, "processos", "sites (8088)")[0]["status"], "OK")

    def test_cli_works_even_when_env_is_broken(self):
        with fake_root("CDBS_SITE_PORT=nao-e-numero\n") as root:
            for name in ("catalog.py", "backup.py", "backup_crypto.py", "audit.py", "lifecycle.py"):
                shutil.copy2(ROOT / name, root / name)
            done = subprocess.run([sys.executable, "cdbs.py", "doctor", "--offline", "--json"], cwd=root,
                                  capture_output=True, text=True, timeout=60)
            self.assertEqual(done.returncode, 1, done.stderr)        # tem FALHA (porta inválida), mas não quebrou
            data = json.loads(done.stdout)
            self.assertEqual(checks(data, "configuração", "portas")[0]["status"], "FALHA")
            self.assertNotIn("Traceback", done.stderr)
            bad = subprocess.run([sys.executable, "cdbs.py", "doctor", "--opcao-errada"], cwd=root, capture_output=True, text=True, timeout=60)
            self.assertEqual(bad.returncode, 2)
            self.assertIn("Opção desconhecida", bad.stderr)


if __name__ == "__main__":
    unittest.main()
