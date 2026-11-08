"""Testes da Central de backups (Fase 1 / Evolução 2): criar, validar, restaurar com reversão, exportar, importar,
lembrete mensal, espaço, isolamento entre lojas e segurança. Sem rede."""
import contextlib
import hashlib
import io
import json
import os
import shutil
import stat
import sys
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import backup
import backup_crypto
import catalog
import cdbs
from backup import BackupError, BackupService
from test_catalog import JPEG, PNG, request, stack

DAY = 86400
PW = "senha-de-recuperacao-teste-1"           # senha de recuperação usada nos testes (nunca é uma senha real)
FAST_KDF = backup_crypto.KdfParams(10, 8, 1)   # parâmetros rápidos SÓ nos testes; o padrão real é N=2^15
INDEX = b"<!doctype html><title>Loja</title><h1>Versao 1</h1>"
CSS = b"body{color:#036}"


class Clock:
    def __init__(self):
        self.t = 1_800_000_000.0

    def now(self):
        return self.t


def tree_hash(folder: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(Path(folder).rglob("*")):
        digest.update(str(path.relative_to(folder)).encode())
        if path.is_file():
            digest.update(path.read_bytes())
    return digest.hexdigest()


class Env:
    def __init__(self, folder: str):
        self.root = Path(folder)
        self.sites = self.root / "sites"
        self.sites.mkdir()
        self.cats = self.root / "catalogs"
        self.clock = Clock()
        self.free = [10 ** 12]
        self.svc = BackupService(self.root, self.sites, self.cats, clock=self.clock.now, disk_free=lambda: self.free[0], kdf=FAST_KDF)
        self.svc.setup_protection(PW, PW)
        self.catalog = catalog.Catalog(self.cats, self.root / "secret.key")

    def site(self, slug="loja-a", files=None):
        folder = self.sites / slug
        folder.mkdir(parents=True, exist_ok=True)
        for name, data in (files or {"index.html": INDEX, "css/style.css": CSS}).items():
            target = folder / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        return folder

    def store(self, slug="loja-a", name="Loja A", products=2):
        password = self.catalog.create_store(slug, name, "(85) 99999-9999")
        for i in range(products):
            product = self.catalog.add_product(slug, {"name": f"Produto {i}", "price": "10,00", "category": "Geral"})
            self.catalog.set_image(slug, product["id"], JPEG + bytes([i]) * 100)
        return password

    def backup(self, slug="loja-a", *, plain=True):
        """Por padrão cria um backup LEGADO (ZIP comum, como o da Evolução 2): os testes antigos continuam valendo para esse caminho.
        plain=False cria o backup protegido (.cdbk) — coberto em test_protecao.py e nos testes marcados Protegido."""
        if plain:
            rec, created = self.svc.start_backup(slug, plaintext=True)
        else:
            rec, created = self.svc.start_backup(slug, PW)
        self.svc.wait(rec["id"])
        return self.svc.get(slug, rec["id"])

    def restore(self, slug, rid, text=None, password=PW, confirm_recreate=True):
        summary = self.svc.restore_summary(slug, rid)
        op, _ = self.svc.start_restore(slug, rid, summary["confirm_token"], text or slug, password, confirm_recreate)
        self.svc.wait(op["id"])
        return self.svc.get(slug, op["id"]), summary

    def snapshot(self, slug="loja-a"):
        return (tree_hash(self.sites / slug) if (self.sites / slug).exists() else "",
                tree_hash(self.cats / slug / "media"), (self.cats / slug / "products.json").read_bytes() if (self.cats / slug / "products.json").exists() else b"")


@contextlib.contextmanager
def env():
    with tempfile.TemporaryDirectory() as folder:
        yield Env(folder)


def zip_entries(path: Path) -> dict:
    with zipfile.ZipFile(path) as z:
        return {n: z.read(n) for n in z.namelist()}


def write_zip(path: Path, entries: dict) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in entries.items():
            z.writestr(name, data)


class CreateTests(unittest.TestCase):
    def test_create_valid_backup_is_recoverable_and_private(self):
        with env() as e:
            e.site()
            e.store()
            rec = e.backup()
            self.assertEqual(rec["state"], "CONCLUIDO", rec)
            self.assertTrue(rec["validation"]["ok"])
            self.assertEqual(rec["parts"], ["catalog", "site"])
            zpath = e.root / "backups" / "loja-a" / f"{rec['id']}.zip"
            self.assertEqual(stat.S_IMODE(zpath.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(zpath.parent.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE((e.root / "backups").stat().st_mode), 0o700)
            entries = zip_entries(zpath)
            manifest = json.loads(entries["manifest.json"])
            self.assertEqual((manifest["format"], manifest["version"], manifest["slug"], manifest["id"]), ("cdbs-backup", 1, "loja-a", rec["id"]))
            for item in manifest["files"]:
                self.assertEqual(hashlib.sha256(entries[item["path"]]).hexdigest(), item["sha256"])
                self.assertEqual(len(entries[item["path"]]), item["size"])
            self.assertIn("site/index.html", entries)
            self.assertIn("site/css/style.css", entries)
            self.assertIn("catalog/products.json", entries)
            self.assertEqual(sum(1 for n in entries if n.startswith("catalog/media/")), 2)
            self.assertEqual(rec["size_bytes"], zpath.stat().st_size)
            self.assertIsNotNone(rec["duration_s"])
            self.assertEqual([s["state"] for s in rec["steps"]], ["CRIANDO", "VALIDANDO", "CONCLUIDO"])

    def test_site_only_and_catalog_only_and_nothing(self):
        with env() as e:
            e.site("so-site")
            self.assertEqual(e.backup("so-site")["parts"], ["site"])
            e.store("so-catalogo", products=1)
            self.assertEqual(e.backup("so-catalogo")["parts"], ["catalog"])
            e.store("vazia", products=0)
            only = e.backup("vazia")
            self.assertEqual(only["state"], "CONCLUIDO")
            with self.assertRaises(BackupError):
                e.svc.start_backup("nao-existe")

    def test_never_includes_secrets(self):
        with env() as e:
            e.site(files={"index.html": INDEX, ".env": b"TOKEN=abc", "id_rsa": b"x", "k.pem": b"x", "store.json": b"{}", ".git/config": b"x",
                          "segredo.key": b"x", "admin.json": b"{}"})
            password = e.store()
            os.symlink(e.root / "secret.key" if (e.root / "secret.key").exists() else e.root, e.sites / "loja-a" / "link")
            rec = e.backup()
            self.assertEqual(rec["state"], "CONCLUIDO")
            entries = zip_entries(e.root / "backups" / "loja-a" / f"{rec['id']}.zip")
            names = set(entries)
            for bad in ("site/.env", "site/id_rsa", "site/k.pem", "site/store.json", "site/.git/config", "site/segredo.key", "site/admin.json", "site/link"):
                self.assertNotIn(bad, names)
            self.assertNotIn("catalog/store.json", names)
            blob = b"".join(entries.values())
            store_json = json.loads((e.cats / "loja-a" / "store.json").read_text())
            self.assertNotIn(store_json["password"]["hash"].encode() if "hash" in store_json["password"] else b"\0", blob)
            self.assertNotIn(password.encode(), blob)
            info = json.loads(entries["catalog/store_info.json"])
            self.assertEqual(set(info), {"name", "whatsapp", "created"})
            self.assertGreaterEqual(rec["skipped"], 6)

    def test_list_only_the_right_store(self):
        with env() as e:
            e.site("loja-a")
            e.site("loja-b", files={"index.html": b"B"})
            a, b = e.backup("loja-a"), e.backup("loja-b")
            self.assertEqual([x["id"] for x in e.svc.list("loja-a")["backups"]], [a["id"]])
            self.assertEqual([x["id"] for x in e.svc.list("loja-b")["backups"]], [b["id"]])

    def test_failure_during_creation_is_recorded_and_leaves_no_partial_file(self):
        with env() as e:
            e.site()
            before = tree_hash(e.sites)

            def boom(count):
                if count == 1:
                    raise OSError("disco falhou /root/segredo/caminho")

            e.svc.hooks["during_create"] = boom
            rec = e.backup()
            self.assertEqual(rec["state"], "FALHOU")
            self.assertEqual(rec["error_code"], "FALHA_CRIACAO")
            self.assertNotIn("segredo", json.dumps(rec))
            self.assertEqual(list((e.root / "backups" / "loja-a").glob("*.zip")), [])
            self.assertEqual(list((e.root / "backups" / "loja-a").glob(".*.part")), [])
            self.assertEqual(tree_hash(e.sites), before)
            e.svc.hooks.clear()
            self.assertEqual(e.backup()["state"], "CONCLUIDO")             # e a loja continua podendo fazer backup

    def test_concurrent_operations_do_not_conflict(self):
        with env() as e:
            e.site()
            gate, entered = threading.Event(), threading.Event()
            e.svc.hooks["during_create"] = lambda n: (entered.set(), gate.wait(10))
            first, created = e.svc.start_backup("loja-a", PW)
            entered.wait(5)
            second, created2 = e.svc.start_backup("loja-a", PW)
            self.assertEqual((created, created2, second["id"]), (True, False, first["id"]))
            with self.assertRaises(BackupError) as ctx:                      # restauração não entra no meio
                e.svc.start_restore("loja-a", first["id"], "x", "loja-a")
            self.assertIn(ctx.exception.code, ("CONFIRMACAO", "EM_ANDAMENTO"))
            gate.set()
            e.svc.wait(first["id"])
            self.assertEqual(len(e.svc.list("loja-a")["backups"]), 1)
            self.assertEqual(e.svc.get("loja-a", first["id"])["state"], "CONCLUIDO")

    def test_no_space_cancels_before_anything_is_written(self):
        with env() as e:
            e.site()
            e.free[0] = 1024
            rec = e.backup()
            self.assertEqual(rec["state"], "FALHOU")
            self.assertEqual(rec["error_code"], "ESPACO_INSUFICIENTE")
            self.assertIn("MB livres", rec["error_message"])
            self.assertEqual(list((e.root / "backups" / "loja-a").glob("*.zip")), [])

    def test_space_query_failure_is_logged_and_does_not_block(self):
        with env() as e:
            e.site()
            e.svc._disk_free = lambda: (_ for _ in ()).throw(OSError("sem acesso"))
            rec = e.backup()
            self.assertEqual(rec["state"], "CONCLUIDO")
            self.assertTrue(rec["warnings"])
            self.assertTrue(any(ev["kind"] == "falha_espaco" for ev in e.svc.events()))

    def test_safety_limits_and_policy_are_visible(self):
        with env() as e:
            e.site(files={"index.html": INDEX, **{f"a{i}.txt": b"x" for i in range(6)}})
            small = BackupService(e.root, e.sites, e.cats, max_files=3, clock=e.clock.now, disk_free=lambda: 10 ** 12, kdf=FAST_KDF)
            rec, _ = small.start_backup("loja-a", PW)
            small.wait(rec["id"])
            self.assertEqual(small.get("loja-a", rec["id"])["error_code"], "LIMITE_TAMANHO")
            storage = e.svc.storage("loja-a")
            self.assertIn("NÃO entram em nenhuma cota", storage["policy"])
            self.assertIsNone(storage["site_quota_bytes"])
            self.assertGreater(storage["site_bytes"], 0)
            self.assertEqual(storage["backup_bytes"], 0)
            e.backup()
            after = e.svc.storage("loja-a")
            self.assertGreater(after["backup_bytes"], 0)
            self.assertEqual(after["site_bytes"], storage["site_bytes"])       # sites e backups são medidos separadamente


class IntegrityTests(unittest.TestCase):
    def made(self, e):
        e.site()
        e.store(products=1)
        rec = e.backup()
        return rec, e.root / "backups" / "loja-a" / f"{rec['id']}.zip"

    def test_valid_archive_passes_standalone(self):
        with env() as e:
            _, path = self.made(e)
            self.assertTrue(backup.validate_archive(path, "loja-a").ok)

    def test_truncated_backup(self):
        with env() as e:
            rec, path = self.made(e)
            data = path.read_bytes()
            path.write_bytes(data[: len(data) // 2])
            self.assertEqual(backup.validate_archive(path, "loja-a").code, "ARQUIVO_CORROMPIDO")
            self.assertEqual(e.svc.validate("loja-a", rec["id"])["state"], "INVALIDO")           # o registro também percebe
            with self.assertRaises(BackupError):
                e.svc.prepare_export("loja-a", rec["id"])
            with self.assertRaises(BackupError):
                e.svc.restore_summary("loja-a", rec["id"])

    def test_mismatching_hash(self):
        with env() as e:
            rec, path = self.made(e)
            entries = zip_entries(path)
            entries["site/index.html"] = b"<h1>adulterado</h1>"
            write_zip(path, entries)
            self.assertEqual(backup.validate_archive(path, "loja-a").code, "HASH_DIVERGENTE")
            self.assertEqual(e.svc.validate("loja-a", rec["id"])["validation"]["code"], "ARQUIVO_ALTERADO")

    def test_file_missing_from_archive_and_extra_file(self):
        with env() as e:
            _, path = self.made(e)
            base = zip_entries(path)
            missing = {k: v for k, v in base.items() if k != "site/css/style.css"}
            write_zip(path, missing)
            self.assertEqual(backup.validate_archive(path, "loja-a").code, "ARQUIVO_AUSENTE")
            write_zip(path, {**base, "site/extra.html": b"x"})
            self.assertEqual(backup.validate_archive(path, "loja-a").code, "INVENTARIO_DIVERGENTE")

    def test_incompatible_version_and_bad_manifest(self):
        with env() as e:
            _, path = self.made(e)
            base = zip_entries(path)
            for version in (2, 0, "1", None):
                manifest = json.loads(base["manifest.json"])
                manifest["version"] = version
                write_zip(path, {**base, "manifest.json": json.dumps(manifest)})
                self.assertEqual(backup.validate_archive(path, "loja-a").code, "VERSAO_INCOMPATIVEL", version)
            write_zip(path, {**base, "manifest.json": "{nao json"})
            self.assertEqual(backup.validate_archive(path, "loja-a").code, "MANIFESTO_INVALIDO")
            without = {k: v for k, v in base.items() if k != "manifest.json"}
            write_zip(path, without)
            self.assertEqual(backup.validate_archive(path, "loja-a").code, "MANIFESTO_INVALIDO")
            path.write_bytes(b"isto nao e um zip")
            self.assertEqual(backup.validate_archive(path, "loja-a").code, "ARQUIVO_CORROMPIDO")
            path.write_bytes(b"")
            self.assertFalse(backup.validate_archive(path, "loja-a").ok)

    def test_backup_of_another_store_is_rejected_everywhere(self):
        with env() as e:
            e.site("loja-a")
            e.site("loja-b", files={"index.html": b"B"})
            rec = e.backup("loja-a")
            zip_a = e.root / "backups" / "loja-a" / f"{rec['id']}.zip"
            self.assertEqual(backup.validate_archive(zip_a, "loja-b").code, "OUTRA_LOJA")
            with self.assertRaises(BackupError) as ctx:                       # importar o ZIP da loja A na loja B
                e.svc.import_archive("loja-b", io.BytesIO(zip_a.read_bytes()), zip_a.stat().st_size)
            self.assertEqual(ctx.exception.code, "BACKUP_INVALIDO")
            self.assertEqual([r for r in e.svc.list("loja-b")["backups"] if r["state"] != "FALHOU"], [])   # só a falha fica registrada
            self.assertFalse(any(r["can_restore"] or r["can_export"] for r in e.svc.list("loja-b")["backups"]))
            self.assertEqual(list((e.root / "backups" / "loja-b").glob("*.zip")), [])
            planted = e.root / "backups" / "loja-b"                             # ZIP da A plantado no lugar de um backup da B
            fake_id = e.svc._new_record("loja-b", "manual")["id"]
            shutil.copy(zip_a, planted / f"{fake_id}.zip")
            e.svc._set("loja-b", fake_id, "VALIDANDO")
            e.svc._finish_validation("loja-b", fake_id, None)
            self.assertEqual(e.svc.get("loja-b", fake_id)["state"], "INVALIDO")

    def test_oversized_declared_or_real_content_is_rejected(self):
        with env() as e:
            _, path = self.made(e)
            base = zip_entries(path)
            manifest = json.loads(base["manifest.json"])
            for item in manifest["files"]:
                if item["path"] == "site/index.html":
                    item["size"] = 3                                          # declara 3 bytes, mas o conteúdo real é maior
            write_zip(path, {**base, "manifest.json": json.dumps(manifest)})
            self.assertEqual(backup.validate_archive(path, "loja-a").code, "HASH_DIVERGENTE")
            self.assertEqual(backup.validate_archive(path, "loja-a", max_bytes=10).code, "LIMITE_EXCEDIDO")
            self.assertEqual(backup.validate_archive(path, "loja-a", max_files=1).code, "LIMITE_EXCEDIDO")

    def test_malicious_paths_are_rejected(self):
        with env() as e:
            _, path = self.made(e)
            base = zip_entries(path)
            manifest = json.loads(base["manifest.json"])
            evil = ["../evil.txt", "/etc/passwd", "site/../../x", "site/a/../../../x", "site\\win.txt", "site/.env", "C:/x", "site//x", "site/dir/"]
            for name in evil:
                with self.subTest(name=name):
                    write_zip(path, {**base, name: b"x"})
                    self.assertFalse(backup.validate_archive(path, "loja-a").ok)
            dupes = e.root / "dupes.zip"
            with zipfile.ZipFile(dupes, "w") as z:
                z.writestr("manifest.json", json.dumps(manifest))
                z.writestr("site/index.html", b"1")
                with self.assertWarns(UserWarning):
                    z.writestr("site/index.html", b"2")
            self.assertFalse(backup.validate_archive(dupes, "loja-a").ok)
            secrets_zip = e.root / "secret.zip"
            write_zip(secrets_zip, {**base, "site/store.json": b"{}"})
            self.assertEqual(backup.validate_archive(secrets_zip, "loja-a").code, "CONTEUDO_PROIBIDO")

    def test_extraction_blocks_zip_slip_even_if_validation_were_bypassed(self):
        with env() as e:
            evil = e.root / "evil.zip"
            write_zip(evil, {"../escapou.txt": b"x"})
            with self.assertRaises(BackupError):
                e.svc._extract(evil, e.root / "dest", [{"path": "../escapou.txt", "size": 1, "sha256": hashlib.sha256(b"x").hexdigest()}])
            self.assertFalse((e.root / "escapou.txt").exists())


class RestoreTests(unittest.TestCase):
    def prepared(self, e):
        e.site()
        password = e.store()
        rec = e.backup()
        original = e.snapshot()
        # estado "atual" diferente do backup
        (e.sites / "loja-a" / "index.html").write_bytes(b"<h1>Versao 2 quebrada</h1>")
        (e.sites / "loja-a" / "novo.html").write_bytes(b"novo")
        product = e.catalog.add_product("loja-a", {"name": "Produto novo", "price": "5"})
        e.catalog.set_image("loja-a", product["id"], PNG + b"1" * 50)
        return rec, original, password

    def test_restore_valid_backup_replaces_state_and_keeps_password(self):
        with env() as e:
            rec, original, password = self.prepared(e)
            broken = e.snapshot()
            op, summary = e.restore("loja-a", rec["id"])
            self.assertEqual(op["state"], "CONCLUIDO", op)
            self.assertTrue(op["restored"]["verified"])
            self.assertEqual(summary["confirm_text"], "loja-a")
            self.assertTrue(summary["will_create_safety_backup"])
            self.assertEqual((e.sites / "loja-a" / "index.html").read_bytes(), INDEX)
            self.assertFalse((e.sites / "loja-a" / "novo.html").exists())
            after = e.snapshot()
            self.assertEqual(after[0], original[0])
            self.assertEqual(after[1], original[1])
            self.assertEqual(json.loads(after[2])["products"], json.loads(original[2])["products"])
            self.assertGreater(json.loads(after[2])["rev"], json.loads(broken[2])["rev"])      # painéis abertos percebem a troca
            self.assertTrue(e.catalog.verify_login("loja-a", password))                         # a senha do cliente não mudou
            safety = e.svc.get("loja-a", op["safety_backup"])
            self.assertEqual((safety["kind"], safety["state"]), ("seguranca", "CONCLUIDO"))
            self.assertEqual(safety["protection"]["state"], "CRIPTOGRAFADO")                       # o backup de segurança também é protegido (2.1)
            self.assertTrue((e.root / "backups" / "loja-a" / f"{safety['id']}.cdbk").is_file())
            self.assertFalse((e.root / "backups" / "loja-a" / f"{safety['id']}.zip").exists())
            self.assertEqual(e.svc.validate("loja-a", safety["id"], PW)["state"], "CONCLUIDO")
            self.assertFalse((e.root / "restore-tmp" / op["id"]).exists())
            self.assertFalse((e.root / "backups" / "loja-a" / "restore-journal.json").exists())
            # o backup de segurança guarda o estado "quebrado": restaurar ele desfaz a restauração
            undo, _ = e.restore("loja-a", safety["id"])
            self.assertEqual(undo["state"], "CONCLUIDO")
            self.assertEqual(e.snapshot()[0], broken[0])

    def test_confirmation_is_required_and_single_use(self):
        with env() as e:
            rec, original, _ = self.prepared(e)
            before = e.snapshot()
            summary = e.svc.restore_summary("loja-a", rec["id"])
            for token, text in (("", "loja-a"), ("falso", "loja-a"), (None, None), (summary["confirm_token"], "outra"), (summary["confirm_token"], "")):
                with self.subTest(token=token, text=text), self.assertRaises(BackupError) as ctx:
                    e.svc.start_restore("loja-a", rec["id"], token, text)
                self.assertEqual(ctx.exception.code, "CONFIRMACAO")
            self.assertEqual(e.snapshot(), before)                               # nada mudou sem confirmação
            summary = e.svc.restore_summary("loja-a", rec["id"])
            e.clock.t += backup.CONFIRM_TTL + 1
            with self.assertRaises(BackupError):
                e.svc.start_restore("loja-a", rec["id"], summary["confirm_token"], "loja-a")
            self.assertEqual(e.snapshot(), before)

    def test_invalid_backup_blocks_restore_and_keeps_current_site(self):
        with env() as e:
            rec, _, _ = self.prepared(e)
            summary = e.svc.restore_summary("loja-a", rec["id"])
            path = e.root / "backups" / "loja-a" / f"{rec['id']}.zip"
            path.write_bytes(path.read_bytes()[:200])                             # estraga depois de pedir o resumo
            before = e.snapshot()
            op, _ = e.svc.start_restore("loja-a", rec["id"], summary["confirm_token"], "loja-a", PW)
            e.svc.wait(op["id"])
            result = e.svc.get("loja-a", op["id"])
            self.assertEqual((result["state"], result["error_code"]), ("FALHOU", "BACKUP_INVALIDO"))
            self.assertEqual(e.snapshot(), before)
            self.assertEqual(e.svc.get("loja-a", rec["id"])["state"], "INVALIDO")
            self.assertEqual([r for r in e.svc.list("loja-a")["backups"] if r["kind"] == "seguranca"], [])

    def test_no_space_cancels_before_touching_the_site(self):
        with env() as e:
            rec, _, _ = self.prepared(e)
            before = e.snapshot()
            summary = e.svc.restore_summary("loja-a", rec["id"])
            e.free[0] = 2048
            op, _ = e.svc.start_restore("loja-a", rec["id"], summary["confirm_token"], "loja-a", PW)
            e.svc.wait(op["id"])
            result = e.svc.get("loja-a", op["id"])
            self.assertEqual((result["state"], result["error_code"]), ("FALHOU", "ESPACO_INSUFICIENTE"))
            self.assertEqual(e.snapshot(), before)
            self.assertFalse((e.root / "restore-tmp").exists() and any((e.root / "restore-tmp").iterdir()))

    def test_failure_while_applying_reverts_to_the_previous_state(self):
        for step in ("site-old-moved", "catalog-media-swapped"):
            with self.subTest(step=step), env() as e:
                rec, _, password = self.prepared(e)
                before = e.snapshot()

                def boom(name):
                    if name == step:
                        raise OSError("falha no meio da troca")

                e.svc.hooks["during_apply"] = boom
                op, _ = e.restore("loja-a", rec["id"])
                self.assertEqual(op["state"], "REVERTIDO", op)
                self.assertEqual(e.snapshot(), before)                            # tudo como estava, site E catálogo
                self.assertTrue(e.catalog.verify_login("loja-a", password))
                self.assertFalse((e.root / "backups" / "loja-a" / "restore-journal.json").exists())
                self.assertFalse((e.root / "restore-tmp" / op["id"]).exists())
                self.assertIn("anterior", op["next_action"])

    def test_failure_before_apply_changes_nothing(self):
        with env() as e:
            rec, _, _ = self.prepared(e)
            before = e.snapshot()
            e.svc.hooks["after_extract"] = lambda: (_ for _ in ()).throw(OSError("disco cheio"))
            op, _ = e.restore("loja-a", rec["id"])
            self.assertEqual(op["state"], "FALHOU")
            self.assertEqual(e.snapshot(), before)

    def test_post_restore_verification_failure_rolls_back(self):
        with env() as e:
            rec, _, _ = self.prepared(e)
            before = e.snapshot()
            e.svc.hooks["before_verify"] = lambda: (e.sites / "loja-a" / "index.html").write_bytes(b"corrompido depois da troca")
            op, _ = e.restore("loja-a", rec["id"])
            self.assertEqual((op["state"], op["error_code"]), ("REVERTIDO", "VERIFICACAO_FALHOU"))
            self.assertEqual(e.snapshot(), before)

    def test_interrupted_restore_is_undone_on_next_start(self):
        with env() as e:
            rec, _, _ = self.prepared(e)
            before = e.snapshot()
            e.svc.hooks["during_apply"] = lambda name: (_ for _ in ()).throw(OSError("processo morto"))
            with patch.object(BackupService, "_rollback", lambda *a: None), patch.object(BackupService, "_cleanup_tmp", lambda *a: None), \
                    patch.object(BackupService, "_restore_failed", lambda *a: None):
                summary = e.svc.restore_summary("loja-a", rec["id"])
                op, _ = e.svc.start_restore("loja-a", rec["id"], summary["confirm_token"], "loja-a", PW)
                e.svc.wait(op["id"])
            self.assertNotEqual(e.snapshot(), before)                             # simulação: o processo morreu no meio da troca
            self.assertTrue((e.root / "backups" / "loja-a" / "restore-journal.json").exists())
            fresh = BackupService(e.root, e.sites, e.cats, clock=e.clock.now, disk_free=lambda: 10 ** 12, kdf=FAST_KDF)
            report = fresh.recover()
            self.assertEqual(report["reverted"], 1)
            self.assertEqual(e.snapshot(), before)
            state = fresh.get("loja-a", op["id"])
            self.assertEqual((state["state"], state["error_code"]), ("REVERTIDO", "INTERROMPIDO"))
            self.assertFalse((e.root / "backups" / "loja-a" / "restore-journal.json").exists())
            self.assertEqual(fresh.recover(), {"interrupted": 0, "reverted": 0, "cleaned": 0})

    def test_interrupted_creation_is_marked_failed_and_part_removed(self):
        with env() as e:
            e.site()
            rec = e.svc._new_record("loja-a", "manual")
            (e.root / "backups" / "loja-a" / f".{rec['id']}.part").write_bytes(b"meio")
            report = e.svc.recover()
            self.assertEqual(report["interrupted"], 1)
            self.assertEqual(e.svc.get("loja-a", rec["id"])["state"], "FALHOU")
            self.assertEqual(list((e.root / "backups" / "loja-a").glob(".*.part")), [])

    def test_removed_store_is_recreated_disabled_without_old_password(self):
        """MUDOU NA 2.1 (de propósito): na Evolução 2 o catálogo de uma loja removida era PULADO. Agora a loja é recriada,
        porém DESATIVADA e sem senha; a senha antiga jamais volta. (Cobertura completa em test_recuperacao.py.)"""
        with env() as e:
            e.site()
            old_password = e.store(products=1)
            rec = e.backup()
            e.catalog.delete_store("loja-a")
            (e.sites / "loja-a" / "index.html").write_bytes(b"depois")
            op, _ = e.restore("loja-a", rec["id"])
            self.assertEqual(op["state"], "CONCLUIDO", op)
            self.assertFalse(op["restored"]["catalog_skipped"])
            self.assertTrue(op["restored"]["recreated_store"])
            self.assertEqual((e.sites / "loja-a" / "index.html").read_bytes(), INDEX)
            store = json.loads((e.cats / "loja-a" / "store.json").read_text())
            self.assertTrue(store["pending"])
            self.assertEqual(store["password"], {})
            self.assertFalse(e.catalog.verify_login("loja-a", old_password))

    def test_restore_works_after_site_was_removed(self):
        with env() as e:
            e.site()
            rec = e.backup()
            shutil.rmtree(e.sites / "loja-a")                                        # o botão "Remover" não leva os backups
            self.assertTrue(e.svc.known("loja-a"))
            self.assertEqual(e.svc.get("loja-a", rec["id"])["state"], "CONCLUIDO")
            op, summary = e.restore("loja-a", rec["id"])
            self.assertEqual(op["state"], "CONCLUIDO")
            self.assertFalse(summary["will_create_safety_backup"])
            self.assertEqual((e.sites / "loja-a" / "index.html").read_bytes(), INDEX)


class ExportImportTests(unittest.TestCase):
    def test_export_only_valid_backup_and_roundtrip_import(self):
        with env() as e:
            e.site()
            rec = e.backup()
            path, size, filename = e.svc.prepare_export("loja-a", rec["id"])
            self.assertEqual((size, path.stat().st_size), (path.stat().st_size, size))
            self.assertRegex(filename, r"^cdbs-backup-loja-a-\d{8}-\d{4}-[0-9a-f]{8}\.zip$")
            self.assertNotIn(str(e.root), filename)
            e.svc.record_export("loja-a", rec["id"], True, size)
            self.assertEqual(e.svc.get("loja-a", rec["id"])["exports"][-1]["ok"], True)
            blob = path.read_bytes()
            shutil.rmtree(e.root / "backups")                                         # "perdeu o telefone": só resta o arquivo exportado
            imported = e.svc.import_archive("loja-a", io.BytesIO(blob), len(blob))
            self.assertEqual((imported["kind"], imported["state"]), ("importado", "CONCLUIDO"))
            self.assertEqual(imported["imported_from"], rec["id"])
            shutil.rmtree(e.sites / "loja-a")
            op, _ = e.restore("loja-a", imported["id"])
            self.assertEqual(op["state"], "CONCLUIDO")
            self.assertEqual((e.sites / "loja-a" / "index.html").read_bytes(), INDEX)

    def test_import_rejects_garbage_incomplete_and_huge(self):
        with env() as e:
            e.site()
            for blob in (b"lixo" * 100, b"PK\x03\x04" + b"\0" * 50):
                with self.assertRaises(BackupError):
                    e.svc.import_archive("loja-a", io.BytesIO(blob), len(blob))
            with self.assertRaises(BackupError):
                e.svc.import_archive("loja-a", io.BytesIO(b"curto"), 1000)
            with self.assertRaises(BackupError):
                e.svc.import_archive("loja-a", io.BytesIO(b""), 0)
            with self.assertRaises(BackupError):
                e.svc.import_archive("loja-a", io.BytesIO(b""), 10 ** 12)
            records = e.svc.list("loja-a")["backups"]
            self.assertEqual(len(records), 3)        # as 3 que chegaram a ser recebidas ficaram registradas (tamanho 0/gigante é recusado antes de gravar)...
            self.assertTrue(all(r["state"] == "FALHOU" and r["error_code"] for r in records))
            self.assertFalse(any(r["can_restore"] or r["can_export"] for r in records))   # ...mas nenhuma é um backup utilizável
            self.assertEqual(list((e.root / "backups" / "loja-a").glob("*.zip")), [])
            self.assertEqual(list((e.root / "backups" / "loja-a").glob(".*.part")), [])

    def test_unvalidated_backup_is_not_exported(self):
        with env() as e:
            e.site()
            rec = e.svc._new_record("loja-a", "manual")
            with self.assertRaises(BackupError) as ctx:
                e.svc.prepare_export("loja-a", rec["id"])
            self.assertEqual(ctx.exception.code, "AINDA_NAO_VALIDADO")


class ReminderTests(unittest.TestCase):
    def test_monthly_reminder_without_duplicates(self):
        with env() as e:
            e.site()
            first = e.svc.reminder("loja-a")                                         # a loja entra na contagem agora
            self.assertFalse(first["overdue"])
            e.clock.t += 29 * DAY
            self.assertFalse(e.svc.reminder("loja-a")["overdue"])
            e.clock.t += 2 * DAY
            for _ in range(10):                                                      # muitas aberturas do painel no mesmo dia
                r = e.svc.reminder("loja-a")
            self.assertTrue(r["overdue"])
            self.assertEqual(r["shown_count"], 1)
            self.assertIn("nunca teve backup", r["message"])
            events = [x for x in e.svc.events("loja-a") if x["kind"] == "lembrete"]
            self.assertEqual(len(events), 1)
            e.clock.t += DAY + 1
            self.assertEqual(e.svc.reminder("loja-a")["shown_count"], 2)             # um novo aviso registrado no dia seguinte
            self.assertEqual(e.svc.overview()["overdue"], 1)

    def test_valid_backup_resets_reminder_and_new_cycle_starts(self):
        with env() as e:
            e.site()
            e.backup()
            e.clock.t += 31 * DAY
            self.assertTrue(e.svc.reminder("loja-a")["overdue"])
            e.backup()
            r = e.svc.reminder("loja-a")
            self.assertFalse(r["overdue"])
            self.assertEqual(r["last_validated_at"], int(e.clock.t))
            e.clock.t += 31 * DAY
            r = e.svc.reminder("loja-a")
            self.assertTrue(r["overdue"])
            self.assertEqual(r["shown_count"], 1)                                    # ciclo novo recomeça a contagem
            self.assertIn("dias sem backup validado", r["message"])

    def test_failed_or_invalid_backup_does_not_count(self):
        with env() as e:
            e.site()
            e.svc.reminder("loja-a")
            e.free[0] = 10
            e.backup()
            e.clock.t += 31 * DAY
            self.assertIsNone(e.svc.last_validated("loja-a"))
            self.assertTrue(e.svc.reminder("loja-a")["overdue"])

    def test_reminder_does_not_block_publication_and_is_listed(self):
        with env() as e:
            e.site()
            e.svc.reminder("loja-a")
            e.clock.t += 40 * DAY
            row = e.svc.overview()["stores"][0]
            self.assertTrue(row["reminder"]["overdue"])
            e.site("loja-a", files={"index.html": b"publicando mesmo assim"})         # nada bloqueia
            self.assertEqual((e.sites / "loja-a" / "index.html").read_bytes(), b"publicando mesmo assim")


class SafetyTests(unittest.TestCase):
    def test_identifiers_and_paths_are_validated(self):
        with env() as e:
            e.site()
            rec = e.backup()
            for slug in ("../x", "a/b", "A", "", "-x", "x" * 40, "..", "loja-a/../../etc"):
                with self.subTest(slug=slug), self.assertRaises(BackupError):
                    e.svc.list(slug)
            for rid in ("../../etc/passwd", "ZZ", "", "0" * 11, "0" * 13, rec["id"].upper()):
                with self.subTest(rid=rid), self.assertRaises(BackupError):
                    e.svc.get("loja-a", rid)

    def test_one_store_cannot_touch_another_stores_backup(self):
        with env() as e:
            e.site("loja-a")
            e.site("loja-b", files={"index.html": b"B"})
            rec = e.backup("loja-a")
            for action in (lambda: e.svc.get("loja-b", rec["id"]), lambda: e.svc.validate("loja-b", rec["id"]),
                           lambda: e.svc.prepare_export("loja-b", rec["id"]), lambda: e.svc.restore_summary("loja-b", rec["id"]),
                           lambda: e.svc.start_restore("loja-b", rec["id"], "x", "loja-b")):
                with self.assertRaises(BackupError):
                    action()
            summary = e.svc.restore_summary("loja-a", rec["id"])
            with self.assertRaises(BackupError) as ctx:                             # o código de A não vale para B
                e.svc.start_restore("loja-b", rec["id"], summary["confirm_token"], "loja-b")
            self.assertIn(ctx.exception.code, ("NAO_ENCONTRADO", "CONFIRMACAO"))
            self.assertEqual((e.sites / "loja-b" / "index.html").read_bytes(), b"B")

    def test_record_with_wrong_slug_inside_is_not_trusted(self):
        with env() as e:
            e.site("loja-a")
            e.site("loja-b", files={"index.html": b"B"})
            rec = e.backup("loja-a")
            moved = e.root / "backups" / "loja-b"
            moved.mkdir(parents=True, exist_ok=True)
            shutil.copy(e.root / "backups" / "loja-a" / f"{rec['id']}.json", moved / f"{rec['id']}.json")
            with self.assertRaises(BackupError):
                e.svc.get("loja-b", rec["id"])

    def test_views_and_logs_never_expose_internal_paths_or_secrets(self):
        with env() as e:
            e.site()
            password = e.store()
            rec = e.backup()
            op, _ = e.restore("loja-a", rec["id"])
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                e.svc.overview()
                e.svc.list("loja-a")
                e.svc.storage()
            everything = json.dumps([e.svc.list("loja-a"), e.svc.overview(), e.svc.events()]) + out.getvalue() + err.getvalue()
            self.assertNotIn(str(e.root), everything)
            self.assertNotIn(password, everything)
            self.assertNotIn(e.catalog._load_store("loja-a")["password"].get("hash", "\0"), everything)

    def test_cleanup_on_start_preserves_sites_and_backups(self):
        with env() as e:
            e.site()
            e.store()
            rec = e.backup()
            (e.root / "backups" / "loja-a" / ".abc123def456.part").write_bytes(b"x")
            before = e.snapshot()
            zips = {p.name: p.read_bytes() for p in (e.root / "backups" / "loja-a").glob("*.zip")}
            e.svc.recover()
            e.svc.recover()
            self.assertEqual(e.snapshot(), before)
            self.assertEqual({p.name: p.read_bytes() for p in (e.root / "backups" / "loja-a").glob("*.zip")}, zips)
            self.assertEqual(e.svc.get("loja-a", rec["id"])["state"], "CONCLUIDO")

    def test_backup_never_deletes_anything_automatically(self):
        with env() as e:
            e.site()
            ids = [e.backup()["id"] for _ in range(4)]
            e.free[0] = 1024                                                         # sem espaço: não apaga backup antigo para abrir lugar
            failed = e.backup()
            self.assertEqual(failed["state"], "FALHOU")
            self.assertEqual({r["id"] for r in e.svc.list("loja-a")["backups"] if r["state"] == "CONCLUIDO"}, set(ids))
            for rid in ids:
                self.assertTrue((e.root / "backups" / "loja-a" / f"{rid}.zip").exists())
            # 2.1: existe exclusão MANUAL (com confirmação), mas nada apaga sozinho — nem a retenção
            self.assertFalse(e.svc.retention_get()["auto_enabled"])
            e.clock.t += 400 * DAY
            e.svc.reminder_tick(); e.svc.overview(); e.svc.retention_preview()
            for rid in ids:
                self.assertTrue((e.root / "backups" / "loja-a" / f"{rid}.zip").exists())


class HttpTests(unittest.TestCase):
    @contextlib.contextmanager
    def admin(self):
        # cada teste começa com o limite de pedidos zerado (o limite real, 90/min, é coberto em test_overview_and_rate_limit)
        with patch.object(cdbs, "BACKUP_GATE", backup.RateGate(100_000, 60)), stack() as urls:
            admin = urls["admin"]
            headers = {"Origin": admin, "X-CDBS-Request": "1"}
            _, setup_headers, _ = request(admin + "/api/setup", "POST", json_body={"password": "senha-do-admin-123"}, headers=headers)
            headers["Cookie"] = setup_headers["set-cookie"].split(";", 1)[0]
            status, _, _ = request(admin + "/api/backups/_protecao/setup", "POST", json_body={"password": PW, "confirm": PW}, headers=headers)
            assert status == 201, status

            def call(method, path, body=None, extra=None, raw=None):
                status, out_headers, data = request(admin + path, method, body=raw, json_body=body, headers={**headers, **(extra or {})})
                try:
                    return status, json.loads(data)
                except ValueError:
                    return status, data

            def raw_get(path):
                return request(admin + path, "GET", headers=headers)

            (cdbs.SITES / "loja-a").mkdir()
            (cdbs.SITES / "loja-a" / "index.html").write_bytes(INDEX)
            yield urls, call, raw_get, headers

    def wait(self, call, path, states, tries=200):
        import time
        for _ in range(tries):
            _, data = call("GET", path)
            record = data.get("backup") or data.get("operation") or data
            if record.get("state") in states:
                return record
            time.sleep(0.05)
        self.fail("tempo esgotado")

    def test_visitor_cannot_use_any_backup_route(self):
        with self.admin() as (urls, call, raw_get, headers):
            _, created = call("POST", "/api/backups/loja-a/create", {"password": PW})
            rid = created["backup"]["id"]
            self.wait(call, f"/api/backups/loja-a/{rid}", {"CONCLUIDO"})
            admin = urls["admin"]
            anon = {"Origin": admin}
            for method, path, body in (("GET", "/api/backups/overview", None), ("GET", "/api/backups/loja-a", None),
                                       ("POST", "/api/backups/loja-a/create", {}), ("GET", f"/api/backups/loja-a/{rid}/download", None),
                                       ("POST", f"/api/backups/loja-a/{rid}/restore/summary", {}), ("POST", f"/api/backups/loja-a/{rid}/restore", {}),
                                       ("POST", f"/api/backups/loja-a/{rid}/validate", {}), ("POST", "/api/backups/loja-a/import", None)):
                with self.subTest(path=path):
                    status, _, _ = request(admin + path, method, json_body=body, headers=anon)
                    self.assertEqual(status, 401)
            for path in ("/api/backups/overview", f"/api/backups/loja-a/{rid}/download", "/backups.js", f"/loja-a/{rid}.zip", "/backups/loja-a", "/_api/backups/overview"):
                status, _, _ = request(urls["public"] + path)
                self.assertEqual(status, 404, path)
            status, _, _ = request(urls["public"] + "/api/backups/loja-a/create", "POST", json_body={}, headers={"Origin": urls["public"]})
            self.assertIn(status, (404, 405))

    def test_origin_is_required_for_changes(self):
        with self.admin() as (urls, call, raw_get, headers):
            for method, path in (("POST", "/api/backups/loja-a/create"), ("POST", "/api/backups/loja-a/import")):
                status, _ = call(method, path, {}, {"Origin": "http://evil.example"})
                self.assertEqual(status, 403)
            self.assertEqual(call("GET", "/api/backups/loja-a")[1]["backups"], [])

    def test_backups_are_unreachable_from_the_public_root(self):
        with self.admin() as (urls, call, raw_get, headers):
            _, created = call("POST", "/api/backups/loja-a/create", {"password": PW})
            rid = created["backup"]["id"]
            self.wait(call, f"/api/backups/loja-a/{rid}", {"CONCLUIDO"})
            self.assertTrue((cdbs.DATA / "backups" / "loja-a" / f"{rid}.cdbk").is_file())
            self.assertNotIn(cdbs.SITES.resolve(), (cdbs.DATA / "backups").resolve().parents)
            for path in (f"/loja-a/../backups/loja-a/{rid}.zip", f"/loja-a/%2e%2e/backups/loja-a/{rid}.zip", f"/backups/loja-a/{rid}.zip",
                         f"/loja-a/{rid}.zip", f"/_media/loja-a/../../backups/loja-a/{rid}.zip", "/loja-a/..%2fbackups"):
                status, _, body = request(urls["public"] + path)
                self.assertNotEqual(status, 200, path)
                self.assertNotIn(b"PK\x03\x04", body[:4])

    def test_full_flow_backup_download_restore_over_http(self):
        with self.admin() as (urls, call, raw_get, headers):
            cdbs.get_catalog().create_store("loja-a", "Loja A", "")
            status, created = call("POST", "/api/backups/loja-a/create", {"password": PW})
            self.assertEqual(status, 202)
            rid = created["backup"]["id"]
            status, again = call("POST", "/api/backups/loja-a/create", {"password": PW})
            self.assertIn(status, (200, 202))
            record = self.wait(call, f"/api/backups/loja-a/{rid}", {"CONCLUIDO", "FALHOU"})
            self.assertEqual(record["state"], "CONCLUIDO", record)
            status, resp_headers, data = raw_get(f"/api/backups/loja-a/{rid}/download")
            self.assertEqual(status, 200)
            self.assertEqual(resp_headers["content-type"], "application/octet-stream")
            self.assertTrue(resp_headers["content-disposition"].endswith('.cdbk"'))
            self.assertTrue(data.startswith(backup_crypto.MAGIC))                               # o que sai do painel é o arquivo protegido
            self.assertIn("attachment", resp_headers["content-disposition"])
            self.assertEqual(resp_headers["cache-control"], "no-store")
            self.assertNotIn(b"PK\x03\x04", data[:8])
            self.assertNotIn(b"index.html", data)                                               # nomes de arquivo não aparecem em claro
            self.assertEqual(call("GET", "/api/backups/loja-b/" + rid + "/download")[0], 404)       # outra loja: não existe
            status, listing = call("GET", "/api/backups/loja-a")
            self.assertEqual(listing["backups"][0]["exports"][-1]["ok"], True)
            # estraga o site e restaura pela API, com confirmação
            (cdbs.SITES / "loja-a" / "index.html").write_bytes(b"quebrado")
            self.assertEqual(call("POST", f"/api/backups/loja-a/{rid}/restore", {})[0], 403)
            self.assertEqual(call("POST", f"/api/backups/loja-a/{rid}/restore", {"confirm_token": "falso", "confirm_slug": "loja-a"})[0], 403)
            self.assertEqual((cdbs.SITES / "loja-a" / "index.html").read_bytes(), b"quebrado")
            status, summary = call("POST", f"/api/backups/loja-a/{rid}/restore/summary", {})
            self.assertEqual(status, 200)
            self.assertNotIn(str(cdbs.DATA), json.dumps(summary))
            status, op = call("POST", f"/api/backups/loja-a/{rid}/restore", {"confirm_token": summary["summary"]["confirm_token"], "confirm_slug": "loja-a", "password": PW})
            self.assertEqual(status, 202)
            done = self.wait(call, f"/api/backups/loja-a/{op['operation']['id']}", {"CONCLUIDO", "FALHOU", "REVERTIDO"})
            self.assertEqual(done["state"], "CONCLUIDO", done)
            self.assertEqual((cdbs.SITES / "loja-a" / "index.html").read_bytes(), INDEX)
            status, _, body = request(urls["public"] + "/loja-a/")                                  # o site continua acessível
            self.assertEqual((status, body), (200, INDEX))

    def test_import_over_http_and_errors_are_generic(self):
        with self.admin() as (urls, call, raw_get, headers):
            _, created = call("POST", "/api/backups/loja-a/create", {"password": PW})
            rid = created["backup"]["id"]
            self.wait(call, f"/api/backups/loja-a/{rid}", {"CONCLUIDO"})
            _, _, data = raw_get(f"/api/backups/loja-a/{rid}/download")
            status, body = call("POST", "/api/backups/loja-a/import", raw=data, extra={"Content-Type": "application/octet-stream", "X-CDBS-Senha": PW})
            self.assertEqual(status, 201)
            self.assertEqual(body["backup"]["kind"], "importado")
            status, body = call("POST", "/api/backups/loja-a/import", raw=b"nao e um zip", extra={"Content-Type": "application/octet-stream", "X-CDBS-Senha": PW})
            self.assertEqual(status, 422)
            self.assertNotIn(str(cdbs.DATA), json.dumps(body))
            (cdbs.SITES / "loja-b").mkdir()
            (cdbs.SITES / "loja-b" / "index.html").write_bytes(b"B")
            status, body = call("POST", "/api/backups/loja-b/import", raw=data, extra={"Content-Type": "application/octet-stream", "X-CDBS-Senha": PW})
            self.assertEqual(status, 422)                                                            # backup de outra loja
            self.assertEqual(call("GET", "/api/backups/%2e%2e/x")[0], 404)
            self.assertEqual(call("GET", "/api/backups/nao-existe")[0], 404)

    def test_site_removal_button_keeps_backups(self):
        with self.admin() as (urls, call, raw_get, headers):
            _, created = call("POST", "/api/backups/loja-a/create", {"password": PW})
            rid = created["backup"]["id"]
            self.wait(call, f"/api/backups/loja-a/{rid}", {"CONCLUIDO"})
            self.assertEqual(call("DELETE", "/api/sites/loja-a")[0], 200)
            self.assertFalse((cdbs.SITES / "loja-a").exists())
            status, listing = call("GET", "/api/backups/loja-a")
            self.assertEqual(status, 200)
            self.assertEqual(listing["backups"][0]["state"], "CONCLUIDO")

    def test_overview_and_rate_limit(self):
        with self.admin() as (urls, call, raw_get, headers):
            status, data = call("GET", "/api/backups/overview")
            self.assertEqual(status, 200)
            self.assertEqual(data["stores"][0]["slug"], "loja-a")
            self.assertIn("NÃO entram em nenhuma cota", data["storage"]["policy"])
            with patch.object(cdbs, "BACKUP_GATE", backup.RateGate(2, 60)):
                codes = [call("POST", "/api/backups/loja-a/validate", {})[0] for _ in range(4)]
            self.assertEqual(codes[2:], [429, 429])

    def _save(self, data):
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder, True)
        path = folder / "baixado.zip"
        path.write_bytes(data)
        return path


if __name__ == "__main__":
    unittest.main()
