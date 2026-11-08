"""3.0 / Parte 2.5 — atualizador: validação do pacote (todos os formatos ruins), preparação isolada, cópia de segurança
conferida, instalação, volta, recuperação de uma atualização interrompida e preservação dos dados byte a byte."""
import contextlib
import hashlib
import io
import json
import os
import stat
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import updater
import versioninfo
from test_scripts import program_files
from test_scripts import snapshot as _all_data


def snapshot(root: Path) -> dict:
    """Dados do usuário (data/ e .env), sem a pasta do próprio atualizador (data/updates/)."""
    return {k: v for k, v in _all_data(root).items() if not k.startswith("data/updates/")}


def package_bytes(*, version="3.0.1", nested=None, extra=None, drop=(), manifest=True, symlink=None, tamper=None, unlisted=None,
                  data_format=1, files=None) -> bytes:
    files = dict(files or program_files())
    files.setdefault("VERSION.json", b"")
    files.pop("VERSION.json")
    for name in drop:
        files.pop(name, None)
    files.update(extra or {})
    if manifest:
        listed = {rel: {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)} for rel, data in files.items()}
        files["VERSION.json"] = json.dumps({"name": "CDBS.OS", "version": version, "released": "2026-10-26", "data_format": data_format, "files": listed}).encode()
    if tamper:
        files[tamper] = files[tamper] + b"\n# alterado depois do manifesto\n"
    if unlisted:
        files.update(unlisted)
    prefix = f"{nested}/" if nested else ""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for rel, data in sorted(files.items()):
            info = zipfile.ZipInfo(prefix + rel)
            info.external_attr = (0o755 if rel.endswith(".sh") else 0o644) << 16
            archive.writestr(info, data)
        if symlink:
            info = zipfile.ZipInfo(prefix + symlink)
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, "/etc/passwd")
    return buffer.getvalue()


@contextlib.contextmanager
def install(version="3.0.0"):
    """Instalação de mentira com manifesto e dados de usuário."""
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder) / "cdbs"
        for rel, data in program_files().items():
            target = root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            if rel.endswith(".sh"):
                target.chmod(0o755)
        (root / "VERSION.json").write_text(json.dumps(versioninfo.build_manifest(root, version, "2026-10-26")))
        (root / ".env").write_text("CDBS_SITE_PORT=8088\nCDBS_WHATSAPP_ACCESS_TOKEN=segredo\n")
        (root / "data" / "sites" / "adega").mkdir(parents=True, exist_ok=True)
        (root / "data" / "sites" / "adega" / "index.html").write_text("<h1>Adega</h1>")
        (root / "data" / "catalogs" / "adega").mkdir(parents=True)
        (root / "data" / "catalogs" / "adega" / "store.json").write_text('{"hash": "x"}')
        (root / "data" / "admin.json").write_text('{"hash": "y"}')
        yield root


def stage(upd, data: bytes) -> dict:
    return upd.stage_upload(io.BytesIO(data), len(data))


class ValidationTests(unittest.TestCase):
    def test_valid_root_and_nested_packages(self):
        with install() as root:
            upd = updater.Updater(root, root / "data")
            for nested in (None, "cdbs_package"):
                with self.subTest(nested=nested):
                    report = stage(upd, package_bytes(nested=nested))
                    self.assertTrue(report["ok"], report["problems"])
                    self.assertEqual(report["version"], "3.0.1")
                    self.assertEqual(report["relation"], "mais nova")
                    self.assertIn("pasta cdbs_package" if nested else "raiz", report["layout"])
                    files = root / "data" / "updates" / "staging" / report["id"] / "files"
                    self.assertTrue((files / "cdbs.py").is_file())
                    self.assertFalse((files / "cdbs_package").exists())
                    self.assertTrue(os.access(files / "start.sh", os.X_OK))
            self.assertEqual(len(upd.staged()), 2)

    def test_structural_problems_are_refused_and_leave_nothing(self):
        cases = [
            ("não é zip", b"nao sou um zip", "não é um ZIP"),
            ("zip cortado", package_bytes()[:5000], "não é um ZIP"),
            ("sem cdbs.py", package_bytes(drop=("cdbs.py",)), "cdbs.py"),
            ("link", package_bytes(symlink="web/x.js"), "links simbólicos"),
            (".env", package_bytes(extra={".env": b"X=1"}), "segredo"),
            ("chave", package_bytes(extra={"tools/servidor.key": b"x"}), "segredo"),
            ("dados", package_bytes(extra={"data/catalogs/x/products.json": b"{}"}), "dados"),
            ("git", package_bytes(extra={".git/config": b"x"}), ".git"),
        ]
        with install() as root:
            upd = updater.Updater(root, root / "data")
            for name, data, needle in cases:
                with self.subTest(case=name), self.assertRaises(updater.UpdateError) as caught:
                    stage(upd, data)
                self.assertIn(needle, str(caught.exception))
            for raw_name in ("../fora.py", "/etc/x", "a/../../b"):
                buffer = io.BytesIO()
                with zipfile.ZipFile(buffer, "w") as archive:
                    archive.writestr("cdbs.py", "x=1")
                    archive.writestr(raw_name, "x")
                with self.subTest(path=raw_name), self.assertRaises(updater.UpdateError):
                    stage(upd, buffer.getvalue())
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w") as archive:
                archive.writestr("cdbs.py", "x=1")
                archive.writestr("Web/App.js", "x")
                archive.writestr("web/app.js", "y")
            with self.assertRaises(updater.UpdateError) as caught:
                stage(upd, buffer.getvalue())
            self.assertIn("repetidos", str(caught.exception))
            self.assertEqual(upd.staged(), [])
            staging = root / "data" / "updates" / "staging"
            self.assertEqual(list(staging.iterdir()) if staging.exists() else [], [], "pacote recusado não deixa sobra")

    def test_soft_problems_are_reported_and_block_installation(self):
        with install() as root:
            upd = updater.Updater(root, root / "data")
            cases = [
                ("sem manifesto", package_bytes(manifest=False), "VERSION.json ausente"),
                ("adulterado", package_bytes(tamper="catalog.py"), "não conferem"),
                ("fora do manifesto", package_bytes(unlisted={"web/extra.js": b"x"}), "divergência"),
                ("sintaxe", package_bytes(extra={"modulo_novo.py": b"def x(:\n"}), "erro de sintaxe"),
                ("formato de dados", package_bytes(data_format=2), "migração"),
                ("obrigatório", package_bytes(drop=("supervisor.py",)), "supervisor.py"),
            ]
            for name, data, needle in cases:
                with self.subTest(case=name):
                    report = stage(upd, data)
                    self.assertFalse(report["ok"])
                    self.assertIn(needle, json.dumps(report, ensure_ascii=False))
                    with self.assertRaises(updater.UpdateError):
                        upd.begin(staged_id=report["id"])
                    upd.discard(report["id"])

    def test_limits_space_and_version_rules(self):
        with install() as root:
            upd = updater.Updater(root, root / "data")
            with patch.object(updater, "MAX_PACKAGE", 1000), self.assertRaises(updater.UpdateError) as caught:
                stage(upd, package_bytes())
            self.assertEqual(caught.exception.status, 413)
            with self.assertRaises(updater.UpdateError):
                upd.stage_upload(io.BytesIO(b"abc"), 10)                    # envio incompleto
            tight = updater.Updater(root, root / "data", disk_free=lambda p: 1024)
            with self.assertRaises(updater.UpdateError) as caught:
                stage(tight, package_bytes())
            self.assertEqual(caught.exception.code, "ESPACO_INSUFICIENTE")
            same = stage(upd, package_bytes(version="3.0.0"))
            older = stage(upd, package_bytes(version="2.9.9"))
            self.assertEqual((same["relation"], older["relation"]), ("igual", "mais antiga"))
            for report in (same, older):
                with self.assertRaises(updater.UpdateError) as caught:
                    upd.begin(staged_id=report["id"])
                self.assertEqual(caught.exception.code, "CONFIRMAR_VERSAO")
            journal = upd.begin(staged_id=older["id"], allow_same_or_older=True)
            self.assertEqual(journal["step"], "VALIDANDO")
            with self.assertRaises(updater.UpdateError):
                upd.discard(older["id"])                                   # em instalação: não descarta
            upd.finish(journal, "FALHOU", "teste")
            no_space = updater.Updater(root, root / "data", disk_free=lambda p: 10)
            with self.assertRaises(updater.UpdateError) as caught:
                no_space.begin(staged_id=same["id"], allow_same_or_older=True)
            self.assertEqual(caught.exception.code, "ESPACO_INSUFICIENTE")


class ApplyTests(unittest.TestCase):
    def test_snapshot_install_and_restore_preserve_data_byte_for_byte(self):
        with install() as root:
            before_data = snapshot(root)
            upd = updater.Updater(root, root / "data")
            (root / "programa_antigo.py").write_text("x = 1\n")                       # existe só na versão instalada
            manifest = versioninfo.build_manifest(root, "3.0.0", "2026-10-26")
            (root / "VERSION.json").write_text(json.dumps(manifest))
            old_cdbs = (root / "cdbs.py").read_bytes()
            new_files = program_files()
            new_files["cdbs.py"] = new_files["cdbs.py"] + b"\n# versao 3.0.1\n"
            new_files["modulo_novo.py"] = b"Y = 2\n"
            report = stage(upd, package_bytes(files=new_files))
            journal = upd.begin(staged_id=report["id"])
            upd.verify_source(journal)
            upd.make_snapshot(journal)
            snap = upd.snapshots()[0]
            self.assertEqual(snap["version"], "3.0.0")
            upd.install(journal)
            self.assertEqual(versioninfo.load(root)["version"], "3.0.1")
            self.assertIn(b"# versao 3.0.1", (root / "cdbs.py").read_bytes())
            self.assertTrue((root / "modulo_novo.py").is_file())
            self.assertFalse((root / "programa_antigo.py").exists())
            self.assertEqual(snapshot(root), before_data, ".env e data/ não mudam numa atualização")
            self.assertTrue(versioninfo.verify(root)["ok"])
            upd.restore(journal)
            upd.finish(journal, "REVERTIDO", "teste")
            self.assertEqual(versioninfo.load(root)["version"], "3.0.0")
            self.assertEqual((root / "cdbs.py").read_bytes(), old_cdbs)
            self.assertFalse((root / "modulo_novo.py").exists())
            self.assertTrue((root / "programa_antigo.py").exists())
            self.assertEqual(snapshot(root), before_data)
            self.assertEqual(upd.history()[0]["step"], "REVERTIDO")
            self.assertTrue(os.access(root / "start.sh", os.X_OK))

    def test_tampered_staging_is_caught_before_installing(self):
        with install() as root:
            upd = updater.Updater(root, root / "data")
            report = stage(upd, package_bytes())
            (root / "data" / "updates" / "staging" / report["id"] / "files" / "catalog.py").write_text("# trocado depois da validação\n")
            journal = upd.begin(staged_id=report["id"])
            with self.assertRaises(updater.UpdateError) as caught:
                upd.verify_source(journal)
            self.assertEqual(caught.exception.code, "PACOTE_ALTERADO")

    def test_interrupted_update_is_finished_safely_on_next_start(self):
        for stop_at in ("COPIA_DE_SEGURANCA", "INSTALANDO"):
            with self.subTest(stop_at=stop_at), install() as root:
                before = snapshot(root)
                old_cdbs = (root / "cdbs.py").read_bytes()
                upd = updater.Updater(root, root / "data")
                new_files = program_files()
                new_files["cdbs.py"] = new_files["cdbs.py"] + b"\n# nova\n"
                report = stage(upd, package_bytes(files=new_files))
                journal = upd.begin(staged_id=report["id"])
                upd.make_snapshot(journal)
                if stop_at == "INSTALANDO":
                    def boom(where):
                        if where == "instalado:3":
                            raise KeyboardInterrupt("o Android matou o Termux no meio da cópia")
                    with self.assertRaises(KeyboardInterrupt):
                        upd.install(journal, hook=boom)
                else:
                    upd._set_step(journal, "COPIA_DE_SEGURANCA")
                fresh = updater.Updater(root, root / "data")                       # "próximo bash start.sh"
                result = fresh.recover_interrupted()
                self.assertEqual(result["step"], "REVERTIDO" if stop_at == "INSTALANDO" else "FALHOU")
                self.assertEqual((root / "cdbs.py").read_bytes(), old_cdbs)
                self.assertEqual(versioninfo.load(root)["version"], "3.0.0")
                self.assertEqual(snapshot(root), before)
                self.assertIsNone(fresh.recover_interrupted(), "depois de recuperado não faz de novo")

    def test_rollback_to_snapshot_and_pruning(self):
        with install() as root:
            upd = updater.Updater(root, root / "data", keep_versions=2)
            for patch_number in (1, 2, 3):
                files = program_files()
                files["cdbs.py"] = files["cdbs.py"] + f"\n# 3.0.{patch_number}\n".encode()
                report = stage(upd, package_bytes(version=f"3.0.{patch_number}", files=files))
                journal = upd.begin(staged_id=report["id"])
                upd.make_snapshot(journal)
                upd.install(journal)
                upd.finish(journal, "CONCLUIDO", "ok")
            self.assertEqual(versioninfo.load(root)["version"], "3.0.3")
            snaps = upd.snapshots()
            self.assertEqual([s["version"] for s in snaps], ["3.0.2", "3.0.1"], "guarda as 2 cópias mais recentes")
            journal = upd.begin(snapshot_id=snaps[1]["id"])
            upd.make_snapshot(journal, reason="antes de voltar versão")
            upd.install(journal)
            upd.finish(journal, "CONCLUIDO", "voltou")
            self.assertEqual(versioninfo.load(root)["version"], "3.0.1")
            self.assertIn(b"# 3.0.1", (root / "cdbs.py").read_bytes())
            with self.assertRaises(updater.UpdateError):
                upd.begin(snapshot_id="../../etc")

    def test_lock_prevents_two_updates_at_once(self):
        with install() as root:
            upd = updater.Updater(root, root / "data")
            first, second = upd.lock(), upd.lock()
            self.assertTrue(first.acquire(timeout=0.1, owner="atualizacao"))
            self.assertFalse(second.acquire(timeout=0.2))
            first.release()


if __name__ == "__main__":
    unittest.main()
