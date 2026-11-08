"""3.0 / Parte 1.2 — scripts do Termux: sintaxe, pré-requisitos ausentes, caminhos com espaço, códigos de saída e
atualizar.sh (formato na raiz e dentro de uma pasta, pacotes perigosos recusados, dados preservados, volta atrás).
Nenhum teste toca a instalação real nem a rede."""
import contextlib
import hashlib
import io
import json
import os
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import versioninfo

SCRIPTS = sorted(p.name for p in ROOT.glob("*.sh"))


def program_files() -> dict[str, bytes]:
    manifest = versioninfo.build_manifest(ROOT, "0.0.0", "")
    return {rel: (ROOT / rel).read_bytes() for rel in manifest["files"]}


def build_package(path: Path, *, version="9.9.9", nested=None, extra=None, drop=(), manifest=True, symlink=None, tamper=None) -> Path:
    files = program_files()
    for name in drop:
        files.pop(name, None)
    files.update(extra or {})
    if manifest:
        listed = {rel: {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)} for rel, data in files.items()}
        files["VERSION.json"] = json.dumps({"name": "CDBS.OS", "version": version, "released": "2026-10-26",
                                            "data_format": 1, "files": listed}).encode()
    if tamper:
        files[tamper] = files[tamper] + b"\n# adulterado depois do manifesto\n"
    prefix = f"{nested}/" if nested else ""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for rel, data in sorted(files.items()):
            info = zipfile.ZipInfo(prefix + rel)
            info.external_attr = (0o755 if rel.endswith(".sh") else 0o644) << 16
            archive.writestr(info, data)
        if symlink:
            info = zipfile.ZipInfo(prefix + symlink)
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, "/etc/passwd")
    return path


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def snapshot(root: Path) -> dict[str, str]:
    """Impressão digital de tudo que é DADO do usuário (data/ e .env)."""
    out = {}
    for path in [root / ".env", *sorted((root / "data").rglob("*"))]:
        if path.is_file():
            out[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


@contextlib.contextmanager
def installation():
    """Instalação de mentira, numa pasta COM ESPAÇO no nome, com dados de um usuário."""
    with tempfile.TemporaryDirectory() as folder:
        base = Path(folder)
        root = base / "meu cdbs"
        root.mkdir()
        for rel, data in program_files().items():
            target = root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        for script in root.glob("*.sh"):
            script.chmod(0o755)
        site, admin = free_port(), free_port()
        (root / ".env").write_text(f"CDBS_SITE_PORT={site}\nCDBS_ADMIN_PORT={admin}\n", encoding="utf-8")
        (root / "data" / "sites" / "adega").mkdir(parents=True)
        (root / "data" / "sites" / "adega" / "index.html").write_text("<h1>Adega</h1>")
        (root / "data" / "catalogs" / "adega").mkdir(parents=True)
        (root / "data" / "catalogs" / "adega" / "products.json").write_text('{"products": []}')
        (root / "data" / "admin.json").write_text('{"hash": "x"}')
        (root / "data" / "secret.key").write_bytes(os.urandom(32))
        (root / "data" / "backups" / "adega").mkdir(parents=True)
        (root / "data" / "backups" / "adega" / "abc.cdbk").write_bytes(b"CDBSBK" + os.urandom(64))
        yield base, root, {"site": site, "admin": admin}


def run_script(script: Path, *args, env=None, timeout=120):
    full_env = {**os.environ, **(env or {})}
    return subprocess.run(["bash", str(script), *map(str, args)], capture_output=True, text=True, timeout=timeout, env=full_env)


class SyntaxTests(unittest.TestCase):
    def test_every_script_passes_bash_n_and_is_executable_in_package(self):
        self.assertGreaterEqual(len(SCRIPTS), 4)
        for name in SCRIPTS:
            with self.subTest(script=name):
                done = subprocess.run(["bash", "-n", str(ROOT / name)], capture_output=True, text=True)
                self.assertEqual(done.returncode, 0, done.stderr)


class StartAndDiagnosticScriptTests(unittest.TestCase):
    def test_start_verify_and_missing_python(self):
        with installation() as (base, root, _):
            ok = run_script(root / "start.sh", "--verificar")
            self.assertEqual(ok.returncode, 0, ok.stderr)
            self.assertIn("Verificação OK", ok.stdout)
            tools = base / "bin-sem-python"
            tools.mkdir()
            os.symlink(shutil.which("dirname"), tools / "dirname")
            missing = subprocess.run([shutil.which("bash"), str(root / "start.sh"), "--verificar"], capture_output=True, text=True,
                                     env={"PATH": str(tools)}, timeout=30)
            self.assertEqual(missing.returncode, 3)
            self.assertIn("pkg install python", missing.stderr)
            (root / "cdbs.py").unlink()
            gone = run_script(root / "start.sh", "--verificar")
            self.assertEqual(gone.returncode, 3)
            self.assertIn("cdbs.py", gone.stderr)

    def test_diagnostico_wrapper_outputs_json_without_secrets(self):
        with installation() as (_, root, _ports):
            token = "EAAGsegredoDEteste0123456789abcdefABCDEF"
            with open(root / ".env", "a", encoding="utf-8") as handle:
                handle.write(f"CDBS_WHATSAPP_ACCESS_TOKEN={token}\n")
            done = run_script(root / "diagnostico.sh", "--offline", "--json")
            self.assertIn(done.returncode, (0, 1), done.stderr)
            data = json.loads(done.stdout)
            self.assertIn("checks", data)
            self.assertNotIn(token, done.stdout + done.stderr)
            text = run_script(root / "diagnostico.sh", "--offline")
            self.assertIn("== CDBS.OS — diagnóstico", text.stdout)


class UpdateScriptTests(unittest.TestCase):
    def assert_refused(self, result, needle):
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(needle, result.stderr)
        self.assertIn("Nada foi alterado", result.stderr)

    def test_root_format_update_preserves_data_and_can_be_undone(self):
        with installation() as (base, root, _):
            before = snapshot(root)
            (root / "arquivo-antigo-do-programa.py").write_text("x = 1\n")
            old_manifest = versioninfo.build_manifest(root, "3.0.0", "2026-10-26")
            (root / "VERSION.json").write_text(json.dumps(old_manifest))
            package = build_package(base / "pacote.zip", version="3.0.1")
            done = run_script(root / "atualizar.sh", package)
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            self.assertIn("Atualizado. Seus dados não foram tocados.", done.stdout)
            self.assertEqual(snapshot(root), before, "data/ e .env têm de ficar idênticos byte a byte")
            self.assertEqual(json.loads((root / "VERSION.json").read_text())["version"], "3.0.1")
            self.assertFalse((root / "arquivo-antigo-do-programa.py").exists(), "arquivo de programa que saiu da versão nova é removido")
            self.assertTrue(os.access(root / "start.sh", os.X_OK))
            copies = sorted(base.glob("meu cdbs-copia-*"))
            self.assertEqual(len(copies), 1)
            self.assertEqual(snapshot(copies[0]), before)
            self.assertTrue((copies[0] / "arquivo-antigo-do-programa.py").exists())
            back = run_script(root / "atualizar.sh", "--voltar", copies[0])
            self.assertEqual(back.returncode, 0, back.stderr)
            self.assertEqual(json.loads((root / "VERSION.json").read_text())["version"], "3.0.0")
            self.assertTrue((root / "arquivo-antigo-do-programa.py").exists())
            self.assertEqual(len(list(base.glob("meu cdbs-desfeita-*"))), 1, "a versão desfeita é guardada, não apagada")

    def test_update_that_brings_a_different_atualizar_sh_finishes_cleanly(self):
        # O pacote traz um atualizar.sh DIFERENTE (outro tamanho). O passo 5 troca o próprio script que está rodando;
        # isso não pode interromper o fim com "syntax error" nem esconder o comando de voltar atrás.
        with installation() as (base, root, _):
            before = snapshot(root)
            original = (ROOT / "atualizar.sh").read_bytes()
            changed = original.replace(b"\n", b"\n" + b"# linha a mais na versao nova\n" * 40, 1)   # logo depois do #!
            self.assertNotEqual(changed, original)
            package = build_package(base / "pacote.zip", version="3.0.3", extra={"atualizar.sh": changed})
            done = run_script(root / "atualizar.sh", package)
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            self.assertNotIn("syntax error", done.stderr)
            self.assertIn("Para VOLTAR ATRÁS", done.stdout)
            self.assertEqual((root / "atualizar.sh").read_bytes(), changed, "o script novo ficou instalado")
            self.assertEqual(json.loads((root / "VERSION.json").read_text())["version"], "3.0.3")
            self.assertEqual(snapshot(root), before)

    def test_nested_folder_format_is_accepted(self):
        with installation() as (base, root, _):
            before = snapshot(root)
            package = build_package(base / "aninhado.zip", nested="cdbs_package", version="3.0.2")
            done = run_script(root / "atualizar.sh", package)
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            self.assertIn("cdbs_package", done.stdout)
            self.assertEqual(json.loads((root / "VERSION.json").read_text())["version"], "3.0.2")
            self.assertFalse((root / "cdbs_package").exists(), "a pasta do ZIP não pode virar uma subpasta da instalação")
            self.assertEqual(snapshot(root), before)

    def test_dangerous_or_wrong_packages_are_refused_before_touching_anything(self):
        with installation() as (base, root, _):
            before = snapshot(root)
            program_before = (root / "cdbs.py").read_bytes()
            cases = [
                ("com-env.zip", {"extra": {".env": b"CDBS_WHATSAPP_ACCESS_TOKEN=x\n"}}, "segredo"),
                ("com-dados.zip", {"extra": {"data/admin.json": b"{}"}}, "segredo"),
                ("com-catalogo.zip", {"extra": {"data/catalogs/x/products.json": b"{}"}}, "dados"),
                ("sem-cdbs.zip", {"drop": ("cdbs.py",)}, "cdbs.py"),
                ("com-link.zip", {"symlink": "web/link.js"}, "links simbólicos"),
                ("adulterado.zip", {"tamper": "catalog.py"}, "não confere com o VERSION.json"),
                ("com-chave.zip", {"extra": {"config/servidor.pem": b"-----BEGIN"}}, "segredo"),
            ]
            for name, kwargs, needle in cases:
                with self.subTest(case=name):
                    result = run_script(root / "atualizar.sh", build_package(base / name, **kwargs))
                    self.assert_refused(result, needle)
            bad_path = base / "caminho.zip"
            with zipfile.ZipFile(bad_path, "w") as archive:
                archive.writestr("cdbs.py", "x = 1\n")
                archive.writestr("../fora.txt", "x")
            self.assert_refused(run_script(root / "atualizar.sh", bad_path), "caminhos perigosos")
            not_zip = base / "nao-e-zip.zip"
            not_zip.write_bytes(b"isto nao e um zip")
            self.assert_refused(run_script(root / "atualizar.sh", not_zip), "não é um ZIP")
            broken_py = build_package(base / "sintaxe.zip", extra={"novo_modulo.py": b"def quebrado(:\n"})
            self.assert_refused(run_script(root / "atualizar.sh", broken_py), "conferência")
            self.assertEqual(snapshot(root), before)
            self.assertEqual((root / "cdbs.py").read_bytes(), program_before)
            self.assertEqual(list(base.glob("meu cdbs-copia-*")), [], "pacote recusado na conferência do ZIP não gasta espaço com cópia")

    def test_refuses_while_server_or_supervisor_is_running(self):
        with installation() as (base, root, ports):
            package = build_package(base / "pacote.zip")
            with socket.socket() as server:
                server.bind(("127.0.0.1", ports["admin"]))
                server.listen()
                result = run_script(root / "atualizar.sh", package)
            self.assertEqual(result.returncode, 1)
            self.assertIn("o CDBS está rodando", result.stderr)
            fake = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", "supervisor.py"])
            try:
                (root / "data" / "system").mkdir(parents=True, exist_ok=True)
                (root / "data" / "system" / "supervisor.json").write_text(json.dumps({"pid": fake.pid, "started_at": int(time.time())}))
                result = run_script(root / "atualizar.sh", package)
                self.assertEqual(result.returncode, 1)
                self.assertIn("o CDBS está rodando", result.stderr)
            finally:
                fake.kill()
                fake.wait()
            (root / "data" / "system" / "supervisor.json").write_text(json.dumps({"pid": fake.pid, "stopped_at": 1}))
            self.assertEqual(run_script(root / "atualizar.sh", package).returncode, 0)

    def test_missing_tools_give_clear_messages(self):
        with installation() as (base, root, _):
            package = build_package(base / "pacote.zip")
            tools = base / "bin-sem-unzip"
            tools.mkdir()
            for name in ("date", "dirname", "python3", "grep", "tail", "cut", "tr"):
                os.symlink(shutil.which(name), tools / name)
            result = subprocess.run([shutil.which("bash"), str(root / "atualizar.sh"), str(package)], capture_output=True, text=True,
                                    env={"PATH": str(tools)}, timeout=60)
            self.assertEqual(result.returncode, 1)
            self.assertIn("pkg install unzip", result.stderr)
            self.assertIn("diga onde está o ZIP", run_script(root / "atualizar.sh").stderr)
            self.assertIn("não encontrei o arquivo", run_script(root / "atualizar.sh", base / "nao-existe.zip").stderr)
            self.assertIn("a pasta da cópia não existe", run_script(root / "atualizar.sh", "--voltar", base / "nada").stderr)


if __name__ == "__main__":
    unittest.main()
