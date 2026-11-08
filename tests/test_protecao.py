"""Evolução 2.1 — Correção A: backups criptografados, backups legados, migração e senha de recuperação. Sem rede."""
import json
import shutil
import stat
import sys
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import backup
import backup_crypto as bc
import test_backup as tb
from backup import BackupError, BackupService
from test_backup import FAST_KDF, INDEX, PW, env


def all_bytes(root: Path) -> bytes:
    """Tudo o que está em disco na pasta de dados (para provar que um texto NÃO aparece em lugar nenhum)."""
    return b"\0".join(p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file())


class EncryptedCreateTests(unittest.TestCase):
    def test_new_backup_is_encrypted_authenticated_and_private(self):
        with env() as e:
            e.site()
            e.store(products=2)
            rec = e.backup(plain=False)
            self.assertEqual(rec["state"], "CONCLUIDO", rec)
            self.assertEqual(rec["protection"]["state"], "CRIPTOGRAFADO")
            self.assertEqual((rec["protection"]["format"], rec["protection"]["version"], rec["protection"]["cipher"]), ("CDBK", 1, "AES-256-GCM"))
            self.assertIn("scrypt", rec["protection"]["kdf"])
            self.assertTrue(rec["protection"]["needs_password"])
            folder = e.root / "backups" / "loja-a"
            path = folder / f"{rec['id']}.cdbk"
            self.assertTrue(path.is_file())
            self.assertFalse((folder / f"{rec['id']}.zip").exists())              # nunca sobra o ZIP em claro
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            raw = path.read_bytes()
            self.assertTrue(raw.startswith(bc.MAGIC))
            self.assertFalse(zipfile.is_zipfile(path))
            for secret in (b"index.html", INDEX, b"Produto 0", b"manifest.json", b"products.json", b"PK\x03\x04"):
                self.assertNotIn(secret, raw)                                       # conteúdo, nomes e estrutura não aparecem
            self.assertEqual(rec["validation"]["mode"], "protegida")
            self.assertTrue(rec["validation"]["ok"])
            self.assertEqual(list((e.root / "restore-tmp").glob("*")) if (e.root / "restore-tmp").exists() else [], [])
            self.assertEqual([p.name for p in folder.glob(".*part")], [])

    def test_password_is_never_written_anywhere(self):
        with env() as e:
            e.site()
            e.store()
            rec = e.backup(plain=False)
            e.svc.validate("loja-a", rec["id"], PW)
            op, _ = e.restore("loja-a", rec["id"])
            self.assertEqual(op["state"], "CONCLUIDO", op)
            with self.assertRaises(BackupError):
                e.svc.start_backup("loja-a", "senha-errada-qualquer-1")
            dump = all_bytes(e.root)
            self.assertNotIn(PW.encode(), dump)                                    # nem em registros, nem em eventos, nem em protection.json
            self.assertNotIn(b"senha-errada-qualquer-1", dump)
            config = json.loads((e.root / "backups" / "protection.json").read_text())
            self.assertNotIn("password", json.dumps(config).lower())                 # só verificador/sal/parâmetros, nunca a senha
            for event in e.svc.events(limit=500):
                self.assertNotIn(PW, json.dumps(event))
            status = e.svc.protection_status()
            self.assertTrue(status["configured"])
            self.assertNotIn("check", status)
            self.assertNotIn("salt", json.dumps(status))
            self.assertEqual(stat.S_IMODE((e.root / "backups" / "protection.json").stat().st_mode), 0o600)

    def test_header_documents_parameters_and_each_file_has_its_own_salt(self):
        with env() as e:
            e.site()
            a = e.backup(plain=False)
            e.site(files={"index.html": INDEX + b"x"})
            b = e.backup(plain=False)
            pa = e.root / "backups" / "loja-a" / f"{a['id']}.cdbk"
            pb = e.root / "backups" / "loja-a" / f"{b['id']}.cdbk"
            ha, hb = bc.read_header(pa), bc.read_header(pb)
            self.assertEqual((ha["format"], ha["version"], ha["cipher"]), ("CDBK", 1, "AES-256-GCM"))
            self.assertEqual(ha["kdf_params"], FAST_KDF.as_dict())                  # os parâmetros usados ficam no próprio arquivo
            ra, rb = pa.read_bytes()[:bc.HEADER_LEN], pb.read_bytes()[:bc.HEADER_LEN]
            self.assertNotEqual(ra[12:28], rb[12:28])                              # sal diferente em cada arquivo (sem chave fixa)
            self.assertNotEqual(ra[28:32], rb[28:32])                              # prefixo do nonce também

    def test_creation_requires_the_right_password_and_a_configured_protection(self):
        with env() as e:
            e.site()
            for wrong in (None, "", "senha-errada-qualquer-1", 123):
                with self.subTest(wrong=wrong), self.assertRaises(BackupError) as ctx:
                    e.svc.start_backup("loja-a", wrong)
                self.assertEqual(ctx.exception.code, "SENHA_INCORRETA")
            self.assertEqual(e.svc.list("loja-a")["backups"], [])                    # nenhum registro nasce de uma tentativa recusada
        with tb.tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "sites").mkdir()
            svc = BackupService(root, root / "sites", root / "catalogs", kdf=FAST_KDF)
            (root / "sites" / "loja-a").mkdir()
            (root / "sites" / "loja-a" / "index.html").write_bytes(INDEX)
            with self.assertRaises(BackupError) as ctx:
                svc.start_backup("loja-a", PW)
            self.assertEqual(ctx.exception.code, "PROTECAO_NAO_CONFIGURADA")

    def test_failure_during_encryption_leaves_no_partial_file(self):
        for hook in ("before_encrypt", "after_encrypt", "during_create"):
            with self.subTest(hook=hook), env() as e:
                e.site()
                e.svc.hooks[hook] = lambda *a: (_ for _ in ()).throw(OSError("falha simulada"))
                rec = e.backup(plain=False)
                self.assertEqual(rec["state"], "FALHOU")
                folder = e.root / "backups" / "loja-a"
                self.assertEqual([p.name for p in folder.iterdir() if p.suffix in (".cdbk", ".zip") or p.name.endswith(".part")], [])
                tmp = e.root / "restore-tmp"
                self.assertFalse(tmp.exists() and any(tmp.iterdir()))
                e.svc.hooks.clear()
                self.assertEqual(e.backup(plain=False)["state"], "CONCLUIDO")        # a loja continua podendo fazer backup

    def test_no_space_refuses_before_writing(self):
        with env() as e:
            e.site()
            e.free[0] = 1024
            rec = e.backup(plain=False)
            self.assertEqual((rec["state"], rec["error_code"]), ("FALHOU", "ESPACO_INSUFICIENTE"))
            self.assertEqual([p for p in (e.root / "backups" / "loja-a").iterdir() if p.suffix in (".cdbk", ".zip")], [])

    def test_archive_inside_never_contains_secrets(self):
        with env() as e:
            e.site(files={"index.html": INDEX, ".env": b"SEGREDO=1", "admin.json": b"{}", "secret.key": b"k"})
            password = e.store(products=1)
            (e.root / "admin.json").write_text('{"hash":"ADMINHASH"}')
            (e.root / "secret.key").write_bytes(b"CHAVESECRETA")
            rec = e.backup(plain=False)
            plain = e.root / "plain.zip"
            bc.decrypt_file(e.root / "backups" / "loja-a" / f"{rec['id']}.cdbk", plain, PW)
            blob = b"".join(zipfile.ZipFile(plain).read(n) for n in zipfile.ZipFile(plain).namelist())
            names = zipfile.ZipFile(plain).namelist()
            store = json.loads((e.cats / "loja-a" / "store.json").read_text())
            for secret in (b"ADMINHASH", b"CHAVESECRETA", b"SEGREDO=1", password.encode(), store["password"]["hash"].encode(), store["password"]["salt"].encode()):
                self.assertNotIn(secret, blob)
            self.assertFalse(any(n.endswith(("store.json", ".env", "admin.json", "secret.key")) for n in names))


class EncryptedRestoreAndIntegrityTests(unittest.TestCase):
    def prepared(self, e):
        e.site()
        password = e.store(products=2)
        rec = e.backup(plain=False)
        (e.sites / "loja-a" / "index.html").write_bytes(b"quebrado")
        return rec, password

    def test_restore_roundtrip_and_wrong_password_keeps_everything(self):
        with env() as e:
            rec, password = self.prepared(e)
            before = e.snapshot()
            summary = e.svc.restore_summary("loja-a", rec["id"])
            self.assertTrue(summary["needs_password"])
            self.assertTrue(summary["safety_backup_protected"])
            for wrong in (None, "senha-errada-qualquer-1"):
                with self.assertRaises(BackupError) as ctx:
                    e.svc.start_restore("loja-a", rec["id"], summary["confirm_token"], "loja-a", wrong)
                self.assertEqual(ctx.exception.code, "SENHA_INCORRETA")
            self.assertEqual(e.snapshot(), before)                                  # nada mudou
            op, _ = e.svc.start_restore("loja-a", rec["id"], summary["confirm_token"], "loja-a", PW)   # o código de confirmação continua valendo
            e.svc.wait(op["id"])
            done = e.svc.get("loja-a", op["id"])
            self.assertEqual(done["state"], "CONCLUIDO", done)
            self.assertEqual((e.sites / "loja-a" / "index.html").read_bytes(), INDEX)
            self.assertTrue(e.catalog.verify_login("loja-a", password))

    def test_any_altered_byte_is_detected(self):
        with env() as e:
            rec, _ = self.prepared(e)
            path = e.root / "backups" / "loja-a" / f"{rec['id']}.cdbk"
            original = path.read_bytes()
            for position in (5, 20, 40, len(original) // 2, len(original) - 1):     # cabeçalho, parâmetros, meio, tag final
                with self.subTest(position=position):
                    broken = bytearray(original)
                    broken[position] ^= 0x01
                    path.write_bytes(bytes(broken))
                    view = e.svc.validate("loja-a", rec["id"], PW)
                    self.assertEqual(view["state"], "INVALIDO", position)
                    summary_error = None
                    try:
                        e.svc.restore_summary("loja-a", rec["id"])
                    except BackupError as err:
                        summary_error = err.code
                    self.assertIn(summary_error, ("AINDA_NAO_VALIDADO", "BACKUP_INVALIDO"))
                    path.write_bytes(original)
                    self.assertEqual(e.svc.validate("loja-a", rec["id"], PW)["state"], "CONCLUIDO")

    def test_truncation_and_extension_are_detected_without_password(self):
        with env() as e:
            rec, _ = self.prepared(e)
            path = e.root / "backups" / "loja-a" / f"{rec['id']}.cdbk"
            original = path.read_bytes()
            for broken in (original[:100], original[:-16], original + b"lixo"):
                path.write_bytes(broken)
                view = e.svc.validate("loja-a", rec["id"])                          # nível 1: sem senha
                self.assertEqual(view["state"], "INVALIDO")
                path.write_bytes(original)
                self.assertEqual(e.svc.validate("loja-a", rec["id"], PW)["state"], "CONCLUIDO")

    def test_validation_levels_are_honest_about_what_was_checked(self):
        with env() as e:
            rec, _ = self.prepared(e)
            file_only = e.svc.validate("loja-a", rec["id"])
            self.assertEqual(file_only["validation"]["mode"], "arquivo")
            self.assertIn("senha", file_only["validation"]["message"].lower())       # diz que o conteúdo NÃO foi aberto
            full = e.svc.validate("loja-a", rec["id"], PW)
            self.assertEqual(full["validation"]["mode"], "protegida")
            self.assertIsNotNone(full["protection"]["content_verified_at"])

    def test_wrong_password_on_validate_is_refused_and_does_not_invalidate(self):
        with env() as e:
            rec, _ = self.prepared(e)
            with self.assertRaises(BackupError) as ctx:
                e.svc.validate("loja-a", rec["id"], "senha-errada-qualquer-1")
            self.assertEqual(ctx.exception.code, "SENHA_INCORRETA")
            self.assertEqual(e.svc.get("loja-a", rec["id"])["state"], "CONCLUIDO")

    def test_lockout_after_repeated_wrong_passwords(self):
        with env() as e:
            rec, _ = self.prepared(e)
            codes = []
            for _ in range(7):
                with self.assertRaises(BackupError) as ctx:
                    e.svc.validate("loja-a", rec["id"], "senha-errada-qualquer-1")
                codes.append(ctx.exception.code)
            self.assertEqual(codes[:5], ["SENHA_INCORRETA"] * 5)
            self.assertEqual(codes[5:], ["SENHA_BLOQUEADA"] * 2)
            with self.assertRaises(BackupError) as ctx:                              # nem a senha certa entra durante o bloqueio
                e.svc.validate("loja-a", rec["id"], PW)
            self.assertEqual(ctx.exception.code, "SENHA_BLOQUEADA")
            e.clock.t += backup.PASSWORD_LOCK + 1
            self.assertEqual(e.svc.validate("loja-a", rec["id"], PW)["state"], "CONCLUIDO")
            self.assertTrue(any(ev["kind"] == "senha_incorreta" for ev in e.svc.events(limit=100)))

    def test_encrypted_restore_without_library_is_blocked_not_faked(self):
        with env() as e:
            rec, _ = self.prepared(e)
            before = e.snapshot()
            with patch.object(bc, "available", lambda: False):
                with self.assertRaises(BackupError) as ctx:
                    e.svc.restore_summary("loja-a", rec["id"])
                self.assertEqual(ctx.exception.code, "PROTECAO_INDISPONIVEL")
                with self.assertRaises(BackupError) as ctx:
                    e.svc.start_backup("loja-a", PW)
                self.assertEqual(ctx.exception.code, "PROTECAO_INDISPONIVEL")
                self.assertFalse(e.svc.protection_status()["available"])
            self.assertEqual(e.snapshot(), before)


class ExportImportTests(unittest.TestCase):
    def test_export_is_the_encrypted_file_and_reimport_works(self):
        with env() as e:
            e.site()
            e.store()
            rec = e.backup(plain=False)
            path, size, filename = e.svc.prepare_export("loja-a", rec["id"])
            self.assertTrue(filename.endswith(".cdbk"))
            data = path.read_bytes()
            self.assertEqual(len(data), size)
            self.assertTrue(data.startswith(bc.MAGIC))
            import io
            imported = e.svc.import_archive("loja-a", io.BytesIO(data), len(data), PW)
            e.svc.wait(imported["id"]) if imported.get("state") not in ("CONCLUIDO", "FALHOU") else None
            got = e.svc.get("loja-a", imported["id"])
            self.assertEqual((got["kind"], got["state"]), ("importado", "CONCLUIDO"))
            self.assertEqual(got["protection"]["state"], "CRIPTOGRAFADO")

    def test_import_from_another_installation_with_other_password_is_explained(self):
        import io
        with env() as a, env() as b:
            a.site()
            rec = a.backup(plain=False)
            data = (a.root / "backups" / "loja-a" / f"{rec['id']}.cdbk").read_bytes()
            # b nasce com a mesma senha de teste; trocamos o verificador para simular OUTRA instalação
            (b.root / "backups" / "protection.json").unlink()
            b.svc.setup_protection("outra-senha-totalmente-diferente", "outra-senha-totalmente-diferente")
            b.site()
            with self.assertRaises(BackupError) as ctx:
                b.svc.import_archive("loja-a", io.BytesIO(data), len(data), "outra-senha-totalmente-diferente")
            self.assertEqual(ctx.exception.code, "OUTRA_SENHA")
            self.assertNotIn(b"outra-senha", b.root.joinpath("backups", "events.json").read_bytes())
            self.assertEqual([r for r in b.svc.list("loja-a")["backups"] if r["state"] == "CONCLUIDO"], [])

    def test_import_of_encrypted_file_for_another_store_is_rejected(self):
        import io
        with env() as e:
            e.site("loja-a")
            e.site("loja-b")
            rec = e.backup("loja-a", plain=False)
            data = (e.root / "backups" / "loja-a" / f"{rec['id']}.cdbk").read_bytes()
            with self.assertRaises(BackupError) as ctx:
                e.svc.import_archive("loja-b", io.BytesIO(data), len(data), PW)
            self.assertIn(ctx.exception.code, ("BACKUP_INVALIDO", "OUTRA_LOJA"))
            self.assertFalse(any(r["can_restore"] for r in e.svc.list("loja-b")["backups"]))

    def test_import_garbage_and_wrong_password(self):
        import io
        with env() as e:
            e.site()
            rec = e.backup(plain=False)
            data = (e.root / "backups" / "loja-a" / f"{rec['id']}.cdbk").read_bytes()
            for payload, password, expected in ((bc.MAGIC + b"\0" * 100, PW, {"BACKUP_INVALIDO"}), (b"nada", PW, {"BACKUP_INVALIDO"}),
                                                (data, "senha-errada-qualquer-1", {"SENHA_INCORRETA"}), (data, None, {"SENHA_INCORRETA"})):
                with self.subTest(password=password), self.assertRaises(BackupError) as ctx:
                    e.svc.import_archive("loja-a", io.BytesIO(payload), len(payload), password)
                self.assertIn(ctx.exception.code, expected)
            self.assertFalse(any(r["can_restore"] for r in e.svc.list("loja-a")["backups"] if r["id"] != rec["id"]))


class LegacyTests(unittest.TestCase):
    def test_legacy_is_labelled_never_converted_automatically_and_still_works(self):
        with env() as e:
            e.site()
            e.store()
            legacy = e.backup(plain=True)
            self.assertEqual(legacy["protection"]["state"], "LEGADO")
            self.assertIn("LEGADO", legacy["protection"]["label"])
            self.assertIn("SEM criptografia", legacy["protection"]["label"])
            self.assertTrue(legacy["can_protect"])
            zpath = e.root / "backups" / "loja-a" / f"{legacy['id']}.zip"
            before = zpath.read_bytes()
            e.clock.t += 100 * tb.DAY
            e.svc.reminder_tick()
            e.svc.overview()
            e.svc.list("loja-a")
            e.svc.storage()
            e.svc.recover()
            self.assertEqual(zpath.read_bytes(), before)                             # nada converte nem apaga sozinho
            self.assertFalse(list((e.root / "backups" / "loja-a").glob("*.cdbk")))
            self.assertEqual(e.svc.storage("loja-a")["legacy_count"], 1)
            (e.sites / "loja-a" / "index.html").write_bytes(b"quebrado")
            op, _ = e.restore("loja-a", legacy["id"])                                # continua restaurável
            self.assertEqual(op["state"], "CONCLUIDO", op)
            self.assertEqual((e.sites / "loja-a" / "index.html").read_bytes(), INDEX)

    def test_migration_creates_encrypted_copy_and_keeps_original(self):
        with env() as e:
            e.site()
            e.store()
            legacy = e.backup(plain=True)
            zpath = e.root / "backups" / "loja-a" / f"{legacy['id']}.zip"
            original = zpath.read_bytes()
            with self.assertRaises(BackupError) as ctx:
                e.svc.start_protect("loja-a", legacy["id"], "senha-errada-qualquer-1")
            self.assertEqual(ctx.exception.code, "SENHA_INCORRETA")
            op, _ = e.svc.start_protect("loja-a", legacy["id"], PW)
            e.svc.wait(op["id"])
            new = e.svc.get("loja-a", op["id"])
            self.assertEqual((new["kind"], new["state"]), ("migrado", "CONCLUIDO"))
            self.assertEqual(new["protection"]["state"], "CRIPTOGRAFADO")
            self.assertEqual(zpath.read_bytes(), original)                           # o original continua intacto
            self.assertEqual(e.svc.get("loja-a", legacy["id"])["state"], "CONCLUIDO")
            self.assertTrue((e.root / "backups" / "loja-a" / f"{new['id']}.cdbk").is_file())
            with self.assertRaises(BackupError) as ctx:
                e.svc.start_protect("loja-a", new["id"], PW)
            self.assertEqual(ctx.exception.code, "JA_PROTEGIDO")
            (e.sites / "loja-a" / "index.html").write_bytes(b"quebrado")
            done, _ = e.restore("loja-a", new["id"])                                 # a cópia migrada restaura igual
            self.assertEqual(done["state"], "CONCLUIDO", done)
            self.assertEqual((e.sites / "loja-a" / "index.html").read_bytes(), INDEX)

    def test_migration_failure_keeps_original_and_leaves_no_leftovers(self):
        with env() as e:
            e.site()
            legacy = e.backup(plain=True)
            zpath = e.root / "backups" / "loja-a" / f"{legacy['id']}.zip"
            original = zpath.read_bytes()
            with patch.object(bc, "encrypt_file", side_effect=OSError("disco cheio")):
                op, _ = e.svc.start_protect("loja-a", legacy["id"], PW)
                e.svc.wait(op["id"])
            self.assertEqual(e.svc.get("loja-a", op["id"])["state"], "FALHOU")
            self.assertEqual(zpath.read_bytes(), original)
            self.assertEqual([p.name for p in (e.root / "backups" / "loja-a").iterdir() if p.suffix == ".cdbk" or p.name.endswith(".part")], [])

    def test_restore_of_legacy_without_library_works_and_does_not_claim_protection(self):
        with env() as e:
            e.site()
            legacy = e.backup(plain=True)
            (e.sites / "loja-a" / "index.html").write_bytes(b"quebrado")
            with patch.object(bc, "available", lambda: False):
                summary = e.svc.restore_summary("loja-a", legacy["id"])
                self.assertFalse(summary["needs_password"])
                self.assertFalse(summary["safety_backup_protected"])
                op, _ = e.svc.start_restore("loja-a", legacy["id"], summary["confirm_token"], "loja-a")
                e.svc.wait(op["id"])
            self.assertEqual(e.svc.get("loja-a", op["id"])["state"], "CONCLUIDO")
            self.assertEqual((e.sites / "loja-a" / "index.html").read_bytes(), INDEX)


class ProtectionSetupTests(unittest.TestCase):
    def fresh(self, folder):
        root = Path(folder)
        (root / "sites").mkdir()
        return BackupService(root, root / "sites", root / "catalogs", kdf=FAST_KDF)

    def test_setup_rules(self):
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            svc = self.fresh(folder)
            self.assertFalse(svc.protection_status()["configured"])
            for password, confirm in (("curta", "curta"), ("senha-de-recuperacao-teste-1", "outra-coisa-bem-diferente"), (None, None), ("a" * 5000, "a" * 5000)):
                with self.subTest(password=str(password)[:6]), self.assertRaises(BackupError) as ctx:
                    svc.setup_protection(password, confirm)
                self.assertEqual(ctx.exception.code, "SENHA_INVALIDA")
            status = svc.setup_protection(PW, PW)
            self.assertTrue(status["configured"])
            self.assertIn("não pode ser recuperada", status["warning"])               # avisa para guardar a senha
            with self.assertRaises(BackupError) as ctx:
                svc.setup_protection(PW, PW)                                           # não troca em silêncio (trocar quebraria a leitura dos antigos)
            self.assertEqual(ctx.exception.code, "PROTECAO_JA_CONFIGURADA")

    def test_status_without_library_does_not_pretend(self):
        import tempfile
        with tempfile.TemporaryDirectory() as folder, patch.object(bc, "available", lambda: False):
            svc = self.fresh(folder)
            status = svc.protection_status()
            self.assertFalse(status["available"])
            with self.assertRaises(BackupError) as ctx:
                svc.setup_protection(PW, PW)
            self.assertEqual(ctx.exception.code, "PROTECAO_INDISPONIVEL")


if __name__ == "__main__":
    unittest.main()
