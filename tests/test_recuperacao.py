"""Evolução 2.1 — Correções C e D: recuperar uma loja removida e restauração robusta (falhas em cada etapa, interrupção,
concorrência, consistência). Sem rede."""
import hashlib
import json
import shutil
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import backup
import catalog
import cdbs
from backup import BackupError, BackupService
from test_backup import FAST_KDF, INDEX, PW, env, tree_hash
from test_catalog import JPEG, request, stack

MARK = b"<!doctype html><title>Outro</title><h1>Conteudo novo, nao o do backup</h1>"


def media_hashes(e, slug="loja-a"):
    media = e.cats / slug / "media"
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(media.iterdir())} if media.is_dir() else {}


def products_of(e, slug="loja-a"):
    return json.loads((e.cats / slug / "products.json").read_text())["products"]


def full_state(e, slug="loja-a"):
    """Tudo o que a restauração pode tocar nesta loja, comparável antes/depois."""
    store = (e.cats / slug / "store.json")
    return {"site": tree_hash(e.sites / slug) if (e.sites / slug).exists() else None, "media": media_hashes(e, slug),
            "products": (e.cats / slug / "products.json").read_bytes() if (e.cats / slug / "products.json").exists() else None,
            "store": store.read_bytes() if store.exists() else None}


def stage_hook(e, stage):
    """Faz o passo `stage` falhar como se o processo tivesse sido morto ali."""
    def boom(*args):
        raise OSError(f"falha simulada em {stage}")
    if stage in ("site-old-moved", "catalog-old-moved", "catalog-media-swapped"):
        e.svc.hooks["during_apply"] = lambda name: boom() if name == stage else None
    else:
        e.svc.hooks[stage] = boom


COMMON_STAGES = ("after_extract", "before_apply", "site-old-moved", "before_verify")


class RecoverRemovedStoreTests(unittest.TestCase):
    def removed(self, e, plain=False, with_site=True):
        if with_site:
            e.site()
        old_password = e.store(products=2)
        rec = e.backup(plain=plain)
        reference = {"products": products_of(e), "media": media_hashes(e), "site": tree_hash(e.sites / "loja-a") if with_site else None}
        e.catalog.delete_store("loja-a")
        if with_site:
            shutil.rmtree(e.sites / "loja-a")                                     # o botão "Remover" do painel
        return rec, old_password, reference

    def recover(self, e, rec, confirm_recreate=True):
        summary = e.svc.restore_summary("loja-a", rec["id"])
        op, _ = e.svc.start_restore("loja-a", rec["id"], summary["confirm_token"], "loja-a", PW, confirm_recreate)
        e.svc.wait(op["id"])
        return e.svc.get("loja-a", op["id"]), summary

    def test_summary_explains_what_will_and_will_not_come_back(self):
        with env() as e:
            rec, _, _ = self.removed(e)
            summary = e.svc.restore_summary("loja-a", rec["id"])
            self.assertEqual((summary["mode"], summary["recreate_store"]), ("recuperar_loja", True))
            self.assertIn("DESATIVADA", summary["warning"])
            self.assertIn("SEM senha", summary["warning"])
            self.assertIn("nunca voltam", summary["warning"])
            self.assertFalse(summary["current"]["has_catalog"])
            self.assertNotIn(str(e.root), json.dumps(summary))

    def test_requires_explicit_recreate_confirmation(self):
        with env() as e:
            rec, _, _ = self.removed(e)
            summary = e.svc.restore_summary("loja-a", rec["id"])
            for flag in (False, None, "true", 1):
                with self.subTest(flag=flag), self.assertRaises(BackupError) as ctx:
                    e.svc.start_restore("loja-a", rec["id"], summary["confirm_token"], "loja-a", PW, flag)
                self.assertEqual(ctx.exception.code, "CONFIRMACAO")
            with self.assertRaises(BackupError):                                      # nome da loja errado
                e.svc.start_restore("loja-a", rec["id"], summary["confirm_token"], "loja-b", PW, True)
            self.assertFalse((e.cats / "loja-a").exists())
            self.assertFalse((e.sites / "loja-a").exists())

    def test_content_comes_back_but_the_store_stays_disabled_and_without_credentials(self):
        for plain in (False, True):
            with self.subTest(legacy=plain), env() as e:
                rec, old_password, reference = self.removed(e, plain=plain)
                done, summary = self.recover(e, rec)
                self.assertEqual(done["state"], "CONCLUIDO", done)
                self.assertTrue(done["restored"]["recreated_store"])
                self.assertTrue(done["restored"]["pending_password"])
                self.assertIn("nova senha", done["next_action"])
                # conteúdo
                self.assertEqual(products_of(e), reference["products"])
                self.assertEqual(media_hashes(e), reference["media"])
                self.assertEqual(tree_hash(e.sites / "loja-a"), reference["site"])
                # metadados
                store = json.loads((e.cats / "loja-a" / "store.json").read_text())
                self.assertEqual((store["name"], store["slug"]), ("Loja A", "loja-a"))
                self.assertTrue(store["pending"])
                self.assertEqual(store["password"], {})                                # nenhuma credencial antiga
                self.assertNotIn(old_password, (e.cats / "loja-a" / "store.json").read_text())
                # ninguém entra
                self.assertTrue(e.catalog.is_pending("loja-a"))
                self.assertFalse(e.catalog.verify_login("loja-a", old_password))
                self.assertFalse(e.catalog.verify_login("loja-a", ""))
                self.assertFalse(e.catalog.verify_login("loja-a", "qualquer coisa"))
                with self.assertRaises(catalog.CatalogError):
                    e.catalog.public_data("loja-a")
                self.assertIsNone(e.catalog.media_path("loja-a", next(iter(reference["media"]))))
                self.assertEqual(next(s for s in e.catalog.list_stores() if s["slug"] == "loja-a")["pending"], True)
                # nenhum arquivo temporário nem diário sobrando
                self.assertFalse((e.root / "restore-tmp" / done["id"]).exists())
                self.assertFalse((e.root / "backups" / "loja-a" / "restore-journal.json").exists())

    def test_old_sessions_do_not_work_and_a_new_password_activates_the_store(self):
        with env() as e:
            e.site()
            # uma sessão emitida ANTES da remoção (mesma chave secreta do servidor)
            old_password = e.store(products=1)
            old_cookie = e.catalog.make_session("loja-a")
            self.assertEqual(e.catalog.read_session(old_cookie), "loja-a")
            rec = e.backup()
            e.catalog.delete_store("loja-a")
            shutil.rmtree(e.sites / "loja-a")
            done, _ = self.recover(e, rec)
            self.assertEqual(done["state"], "CONCLUIDO", done)
            self.assertIsNone(e.catalog.read_session(old_cookie))                      # sessão antiga morta
            new_cookie_attempt = e.catalog.make_session("loja-a")
            self.assertIsNone(e.catalog.read_session(new_cookie_attempt))              # nem uma sessão nova vale enquanto pendente
            new_password = e.catalog.reset_password("loja-a")                          # a decisão humana que ativa a loja
            self.assertNotEqual(new_password, old_password)
            self.assertFalse(e.catalog.is_pending("loja-a"))
            self.assertTrue(e.catalog.verify_login("loja-a", new_password))
            self.assertFalse(e.catalog.verify_login("loja-a", old_password))
            self.assertIsNone(e.catalog.read_session(old_cookie))
            self.assertEqual(e.catalog.read_session(e.catalog.make_session("loja-a")), "loja-a")
            self.assertEqual(len(e.catalog.public_data("loja-a")["products"]), 1)

    def test_other_stores_are_untouched_and_isolated(self):
        with env() as e:
            e.site("loja-b")
            e.store("loja-b", "Loja B", products=2)
            b_before = full_state(e, "loja-b")
            rec, _, _ = self.removed(e)
            done, _ = self.recover(e, rec)
            self.assertEqual(done["state"], "CONCLUIDO", done)
            self.assertEqual(full_state(e, "loja-b"), b_before)
            self.assertFalse(e.catalog.is_pending("loja-b"))                           # a B segue ativa, com a senha dela
            self.assertEqual([r["id"] for r in e.svc.list("loja-b")["backups"]], [])     # backups da A não aparecem na B

    def test_site_only_removal_keeps_working_as_before(self):
        with env() as e:
            e.site()
            rec = e.backup()
            shutil.rmtree(e.sites / "loja-a")
            summary = e.svc.restore_summary("loja-a", rec["id"])
            self.assertEqual((summary["mode"], summary["recreate_store"]), ("restaurar", False))   # sem catálogo no backup: nada de loja pendente
            done, _ = e.restore("loja-a", rec["id"])
            self.assertEqual(done["state"], "CONCLUIDO")
            self.assertFalse((e.cats / "loja-a").exists())

    def test_catalog_only_backup_recovers_without_site(self):
        with env() as e:
            e.store(products=2)
            rec = e.backup()
            self.assertEqual(rec["parts"], ["catalog"])
            e.catalog.delete_store("loja-a")
            done, _ = self.recover(e, rec)
            self.assertEqual(done["state"], "CONCLUIDO", done)
            self.assertTrue(e.catalog.is_pending("loja-a"))
            self.assertFalse((e.sites / "loja-a").exists())

    def test_an_existing_store_is_never_overwritten_by_recovery(self):
        with env() as e:
            rec, _, _ = self.removed(e)
            summary = e.svc.restore_summary("loja-a", rec["id"])                       # resumo feito quando a loja NÃO existia
            new_password = e.catalog.create_store("loja-a", "Loja Nova", "(85) 98888-7777")   # …e então alguém criou uma loja com o mesmo nome
            before = full_state(e)
            with self.assertRaises(BackupError) as ctx:
                e.svc.start_restore("loja-a", rec["id"], summary["confirm_token"], "loja-a", PW, True)
            self.assertEqual(ctx.exception.code, "SITUACAO_MUDOU")
            self.assertEqual(full_state(e), before)
            self.assertTrue(e.catalog.verify_login("loja-a", new_password))

    def test_store_created_during_the_swap_is_not_overwritten(self):
        with env() as e:
            rec, _, _ = self.removed(e)
            e.site(files={"index.html": MARK})                                         # há um site novo ali: será protegido por backup de segurança
            created = {}

            def sneaky(name):
                if name == "site-old-moved":
                    created["password"] = e.catalog.create_store("loja-a", "Loja Nova", "(85) 98888-7777")
            e.svc.hooks["during_apply"] = sneaky
            done, _ = self.recover(e, rec)
            self.assertEqual(done["state"], "REVERTIDO", done)
            self.assertEqual(done["error_code"], "LOJA_JA_EXISTE")
            self.assertTrue(e.catalog.verify_login("loja-a", created["password"]))    # a loja nova segue intacta e com a senha dela
            self.assertFalse(e.catalog.is_pending("loja-a"))
            self.assertEqual((e.sites / "loja-a" / "index.html").read_bytes(), MARK)   # e o site de antes voltou

    def test_failure_at_every_stage_leaves_no_partial_store(self):
        for stage in COMMON_STAGES + ("catalog-old-moved",):
            for plain in (False, True):
                with self.subTest(stage=stage, legacy=plain), env() as e:
                    rec, _, _ = self.removed(e, plain=plain)
                    e.site(files={"index.html": MARK})
                    before = full_state(e)
                    stage_hook(e, stage)
                    done, _ = self.recover(e, rec)
                    self.assertIn(done["state"], ("REVERTIDO", "FALHOU"), done)
                    self.assertEqual(full_state(e), before, stage)                       # volta EXATAMENTE ao que era
                    self.assertFalse((e.cats / "loja-a").exists())                       # nenhuma loja pela metade
                    self.assertFalse((e.root / "restore-tmp" / done["id"]).exists())
                    self.assertFalse((e.root / "backups" / "loja-a" / "restore-journal.json").exists())
                    self.assertFalse(e.svc.in_maintenance("loja-a"))                     # o aviso de manutenção não fica preso
                    e.svc.hooks.clear()
                    retry, _ = self.recover(e, rec)                                      # e dá para tentar de novo
                    self.assertEqual(retry["state"], "CONCLUIDO", retry)

    def test_invalid_pending_state_after_apply_is_rolled_back(self):
        """Se a verificação final acha a loja diferente do esperado (ex.: com senha), tudo volta."""
        with env() as e:
            rec, _, _ = self.removed(e)

            def tamper():
                path = e.cats / "loja-a" / "store.json"
                data = json.loads(path.read_text())
                data["password"] = {"salt": "x", "hash": "y", "rounds": 1}
                path.write_text(json.dumps(data))
            e.svc.hooks["before_verify"] = tamper
            done, _ = self.recover(e, rec)
            self.assertEqual((done["state"], done["error_code"]), ("REVERTIDO", "VERIFICACAO_FALHOU"))
            self.assertFalse((e.cats / "loja-a").exists())

    def test_interrupted_recovery_is_undone_on_next_start(self):
        for stage in ("before_apply", "site-old-moved", "catalog-old-moved", "before_verify"):
            with self.subTest(stage=stage), env() as e:
                rec, _, _ = self.removed(e)
                e.site(files={"index.html": MARK})
                before = full_state(e)
                stage_hook(e, stage)
                with patch.object(BackupService, "_rollback", lambda *a: None), patch.object(BackupService, "_cleanup_tmp", lambda *a: None), \
                        patch.object(BackupService, "_restore_failed", lambda *a: None):
                    done, _ = self.recover(e, rec)                                       # o processo "morreu": nada desfez
                if stage != "before_apply":                                              # antes da troca ainda não mudou nada
                    self.assertNotEqual(full_state(e), before)
                fresh = BackupService(e.root, e.sites, e.cats, clock=e.clock.now, disk_free=lambda: 10 ** 12, kdf=FAST_KDF)
                report = fresh.recover()
                self.assertGreaterEqual(report["reverted"], 1)
                self.assertEqual(full_state(e), before)
                self.assertFalse((e.cats / "loja-a").exists())
                self.assertFalse(any((e.root / "restore-tmp").iterdir()) if (e.root / "restore-tmp").exists() else False)
                self.assertIn(fresh.get("loja-a", done["id"])["state"], ("REVERTIDO", "FALHOU"))
                self.assertEqual(fresh.recover(), {"interrupted": 0, "reverted": 0, "cleaned": 0})   # idempotente

    def test_no_space_cancels_before_creating_anything(self):
        with env() as e:
            rec, _, _ = self.removed(e)
            summary = e.svc.restore_summary("loja-a", rec["id"])
            e.free[0] = 2048
            op, _ = e.svc.start_restore("loja-a", rec["id"], summary["confirm_token"], "loja-a", PW, True)
            e.svc.wait(op["id"])
            got = e.svc.get("loja-a", op["id"])
            self.assertEqual((got["state"], got["error_code"]), ("FALHOU", "ESPACO_INSUFICIENTE"))
            self.assertFalse((e.cats / "loja-a").exists())
            self.assertFalse((e.sites / "loja-a").exists())


class RestoreRobustnessTests(unittest.TestCase):
    def prepared(self, e, plain=False):
        e.site()
        password = e.store(products=2)
        rec = e.backup(plain=plain)
        (e.sites / "loja-a" / "index.html").write_bytes(MARK)
        e.catalog.add_product("loja-a", {"name": "Depois do backup", "price": "9,00", "category": "Geral"})
        return rec, password

    def test_failure_at_every_stage_restores_the_exact_previous_state(self):
        for stage in COMMON_STAGES + ("catalog-old-moved", "catalog-media-swapped"):
            for plain in (False, True):
                with self.subTest(stage=stage, legacy=plain), env() as e:
                    rec, password = self.prepared(e, plain)
                    before = full_state(e)
                    stage_hook(e, stage)
                    done, _ = e.restore("loja-a", rec["id"])
                    if stage == "catalog-old-moved":                                       # esse passo só existe na recuperação de loja removida
                        self.assertEqual(done["state"], "CONCLUIDO")
                        continue
                    self.assertIn(done["state"], ("REVERTIDO", "FALHOU"), done)
                    after = full_state(e)
                    self.assertEqual(after["site"], before["site"], stage)
                    self.assertEqual(after["media"], before["media"], stage)
                    self.assertEqual(after["products"], before["products"], stage)
                    self.assertEqual(after["store"], before["store"], stage)
                    self.assertTrue(e.catalog.verify_login("loja-a", password))
                    self.assertFalse((e.root / "backups" / "loja-a" / "restore-journal.json").exists())
                    self.assertFalse((e.root / "restore-tmp" / done["id"]).exists())
                    self.assertFalse(e.svc.in_maintenance("loja-a"))
                    e.svc.hooks.clear()
                    retry, _ = e.restore("loja-a", rec["id"])
                    self.assertEqual(retry["state"], "CONCLUIDO", retry)
                    self.assertEqual((e.sites / "loja-a" / "index.html").read_bytes(), INDEX)

    def test_previous_version_is_kept_until_the_new_one_is_confirmed(self):
        with env() as e:
            rec, _ = self.prepared(e)
            seen = {}

            def check():
                tmp = e.root / "restore-tmp"
                folders = [p for p in tmp.iterdir()]
                seen["old_site"] = any((p / "old-site" / "index.html").read_bytes() == MARK for p in folders if (p / "old-site" / "index.html").exists())
                seen["old_media"] = any((p / "old-media").is_dir() for p in folders)
                seen["new_live"] = (e.sites / "loja-a" / "index.html").read_bytes() == INDEX
            e.svc.hooks["before_verify"] = check
            done, _ = e.restore("loja-a", rec["id"])
            self.assertEqual(done["state"], "CONCLUIDO", done)
            self.assertEqual(seen, {"old_site": True, "old_media": True, "new_live": True})   # a versão anterior ainda existia na hora de confirmar
            self.assertFalse(any((e.root / "restore-tmp").iterdir()))                         # e só depois de confirmada some

    def test_safety_backup_is_validated_before_anything_is_touched(self):
        with env() as e:
            rec, _ = self.prepared(e)
            before = full_state(e)
            e.svc.hooks["before_encrypt"] = lambda *a: (_ for _ in ()).throw(OSError("falha no backup de segurança"))
            done, _ = e.restore("loja-a", rec["id"])
            self.assertEqual((done["state"], done["error_code"]), ("FALHOU", "FALHA_BACKUP_SEGURANCA"))
            self.assertEqual(full_state(e), before)                                          # sem backup de segurança, nada é trocado
            self.assertFalse((e.root / "restore-tmp" / done["id"]).exists())

    def test_safety_backup_can_undo_the_restore(self):
        with env() as e:
            rec, _ = self.prepared(e)
            before = full_state(e)
            done, _ = e.restore("loja-a", rec["id"])
            safety = e.svc.get("loja-a", done["safety_backup"])
            self.assertEqual((safety["state"], safety["validation"]["ok"]), ("CONCLUIDO", True))
            self.assertEqual(safety["protection"]["state"], "CRIPTOGRAFADO")
            undo, _ = e.restore("loja-a", safety["id"])
            self.assertEqual(undo["state"], "CONCLUIDO", undo)
            after = full_state(e)
            self.assertEqual((after["site"], after["media"]), (before["site"], before["media"]))
            self.assertEqual(json.loads(after["products"])["products"], json.loads(before["products"])["products"])

    def test_every_step_is_logged_in_the_record(self):
        with env() as e:
            rec, _ = self.prepared(e)
            done, _ = e.restore("loja-a", rec["id"])
            states = [s["state"] for s in done["steps"]]
            for expected in ("VALIDANDO", "PREPARANDO", "APLICANDO", "VERIFICANDO", "CONCLUIDO"):
                self.assertIn(expected, states)
            self.assertEqual(states, sorted(set(states), key=states.index))                     # sem passos repetidos nem fora de ordem

    def test_concurrent_restores_on_the_same_store_are_refused(self):
        with env() as e:
            rec, _ = self.prepared(e)
            gate, entered = threading.Event(), threading.Event()
            e.svc.hooks["after_extract"] = lambda: (entered.set(), gate.wait(10))
            s1 = e.svc.restore_summary("loja-a", rec["id"])
            s2 = e.svc.restore_summary("loja-a", rec["id"])
            first, _ = e.svc.start_restore("loja-a", rec["id"], s1["confirm_token"], "loja-a", PW)
            entered.wait(5)
            with self.assertRaises(BackupError) as ctx:
                e.svc.start_restore("loja-a", rec["id"], s2["confirm_token"], "loja-a", PW)
            self.assertEqual(ctx.exception.code, "EM_ANDAMENTO")
            with self.assertRaises(BackupError) as ctx:                                          # nem backup, nem exclusão, nem proteção no meio
                e.svc.delete_summary("loja-a", rec["id"])
            self.assertEqual(ctx.exception.code, "BACKUP_EM_USO")
            gate.set()
            e.svc.wait(first["id"])
            self.assertEqual(e.svc.get("loja-a", first["id"])["state"], "CONCLUIDO")
            e.svc.hooks.clear()

    def test_restores_on_different_stores_run_independently(self):
        with env() as e:
            ids = {}
            for slug in ("loja-a", "loja-b"):
                e.site(slug)
                ids[slug] = e.backup(slug)["id"]
                (e.sites / slug / "index.html").write_bytes(MARK)
            summaries = {s: e.svc.restore_summary(s, ids[s]) for s in ids}
            ops = {s: e.svc.start_restore(s, ids[s], summaries[s]["confirm_token"], s, PW)[0] for s in ids}
            for op in ops.values():
                e.svc.wait(op["id"])
            for slug in ids:
                self.assertEqual(e.svc.get(slug, ops[slug]["id"])["state"], "CONCLUIDO")
                self.assertEqual((e.sites / slug / "index.html").read_bytes(), INDEX)

    def test_catalog_writes_wait_for_the_swap_and_nothing_is_lost(self):
        """Um cliente salvando produto durante a troca não corrompe nada: ou entra antes (e some junto com a troca) ou depois (e fica)."""
        with env() as e:
            rec, _ = self.prepared(e)
            results = []
            started = threading.Event()

            def during(name):
                if name == "catalog-media-swapped":
                    started.set()
                    worker = threading.Thread(target=lambda: results.append(e.catalog.add_product("loja-a", {"name": "Durante", "price": "1,00", "category": "Geral"})))
                    worker.start()
                    during.worker = worker
            e.svc.hooks["during_apply"] = during
            done, _ = e.restore("loja-a", rec["id"])
            during.worker.join(10)
            self.assertEqual(done["state"], "CONCLUIDO", done)
            names = [p["name"] for p in products_of(e)]
            self.assertIn("Durante", names)                                                      # a escrita feita depois da troca ficou
            self.assertNotIn("Depois do backup", names)
            json.loads((e.cats / "loja-a" / "products.json").read_text())                        # arquivo íntegro

    def test_public_site_is_in_maintenance_not_missing_during_the_swap(self):
        with env() as e:
            rec, _ = self.prepared(e)
            flags = []
            e.svc.hooks["during_apply"] = lambda name: flags.append((name, e.svc.in_maintenance("loja-a"), e.svc.in_maintenance("loja-b")))
            e.svc.hooks["before_apply"] = lambda: flags.append(("before_apply", e.svc.in_maintenance("loja-a"), False))
            done, _ = e.restore("loja-a", rec["id"])
            self.assertEqual(done["state"], "CONCLUIDO")
            during = [f for f in flags if f[0] != "before_apply"]
            self.assertTrue(during and all(a and not b for _, a, b in during))                    # só a loja afetada, só durante a troca
            self.assertEqual([f for f in flags if f[0] == "before_apply"][0][1], False)           # extração e conferência não derrubam o site
            self.assertFalse(e.svc.in_maintenance("loja-a"))


class OrphanTempTests(unittest.TestCase):
    def test_orphan_tmp_folders_are_swept_but_not_ones_a_journal_needs(self):
        with env() as e:
            e.site()
            tmp = e.root / "restore-tmp"
            tmp.mkdir(mode=0o700)
            orphan = tmp / ("a" * 12)
            (orphan / "new").mkdir(parents=True)
            (orphan / "new" / "arquivo").write_bytes(b"texto em claro de um backup")
            needed = tmp / ("b" * 12)
            needed.mkdir()
            (e.root / "backups" / "loja-a").mkdir(parents=True, exist_ok=True)
            journal = {"opid": "b" * 12, "slug": "loja-a", "parts": [], "discard": str(needed / "discard")}
            (e.root / "backups" / "loja-a" / "restore-journal.json").write_text(json.dumps(journal))
            (tmp / "nao-e-uma-operacao").mkdir()
            report = e.svc.recover()
            self.assertFalse(orphan.exists())
            self.assertEqual(report["cleaned"], 1)
            self.assertTrue((tmp / "nao-e-uma-operacao").exists())                                # só mexe no que é nosso
            self.assertFalse((e.root / "backups" / "loja-a" / "restore-journal.json").exists())  # diário foi desfeito/limpo na recuperação
            self.assertEqual(e.svc.recover(), {"interrupted": 0, "reverted": 0, "cleaned": 0})


class RecoveryOverHttpTests(unittest.TestCase):
    """Loja recuperada: invisível ao público (site, catálogo e fotos) e sem login, até receber uma nova senha. Manutenção devolve 503."""

    def setUp(self):
        self._stack = stack()
        self.urls = self._stack.__enter__()
        self.addCleanup(self._stack.__exit__, None, None, None)
        self.svc = cdbs.get_backup()
        self.svc.setup_protection(PW, PW)
        (cdbs.SITES / "loja-a").mkdir()
        (cdbs.SITES / "loja-a" / "index.html").write_bytes(INDEX)
        self.cat = cdbs.get_catalog()
        self.password = self.cat.create_store("loja-a", "Loja A", "(85) 99999-9999")
        product = self.cat.add_product("loja-a", {"name": "Camisa", "price": "10,00", "category": "Geral"})
        self.cat.set_image("loja-a", product["id"], JPEG + b"1" * 50)
        self.media = next((Path(cdbs.DATA) / "catalogs" / "loja-a" / "media").iterdir()).name

    def backup_and_remove(self):
        rec, _ = self.svc.start_backup("loja-a", PW)
        self.svc.wait(rec["id"])
        self.cat.delete_store("loja-a")
        shutil.rmtree(cdbs.SITES / "loja-a")
        return rec

    def recover(self, rec):
        summary = self.svc.restore_summary("loja-a", rec["id"])
        op, _ = self.svc.start_restore("loja-a", rec["id"], summary["confirm_token"], "loja-a", PW, True)
        self.svc.wait(op["id"])
        return self.svc.get("loja-a", op["id"])

    def public(self, path):
        status, headers, body = request(self.urls["public"] + path)
        return status

    def test_pending_store_is_invisible_everywhere_until_password_is_generated(self):
        rec = self.backup_and_remove()
        self.assertEqual(self.public("/loja-a/"), 404)
        done = self.recover(rec)
        self.assertEqual(done["state"], "CONCLUIDO", done)
        for path in ("/loja-a/", "/loja-a/index.html", "/loja-a", "/_api/loja-a/products.json", f"/_media/loja-a/{self.media}"):
            self.assertEqual(self.public(path), 404, path)
        # login do cliente recusado (com a senha antiga e com qualquer outra)
        for password in (self.password, "x" * 12):
            status, _, _ = request(self.urls["client"] + "/api/login", "POST", json_body={"slug": "loja-a", "password": password}, headers={"Origin": self.urls["public"]})
            self.assertEqual(status, 401)
        # administrador vê a loja como pendente
        self.assertTrue(self.cat.is_pending("loja-a"))
        # gerar a senha ativa
        new_password = self.cat.reset_password("loja-a")
        self.assertEqual(self.public("/loja-a/"), 200)
        self.assertEqual(self.public("/_api/loja-a/products.json"), 200)
        self.assertEqual(self.public(f"/_media/loja-a/{self.media}"), 200)
        status, _, _ = request(self.urls["client"] + "/api/login", "POST", json_body={"slug": "loja-a", "password": new_password}, headers={"Origin": self.urls["public"]})
        self.assertEqual(status, 200)
        status, _, _ = request(self.urls["client"] + "/api/login", "POST", json_body={"slug": "loja-a", "password": self.password}, headers={"Origin": self.urls["public"]})
        self.assertEqual(status, 401)

    def test_visitors_get_503_with_retry_after_during_the_swap_never_a_partial_page(self):
        (cdbs.SITES / "loja-a" / "extra.html").write_bytes(b"x")
        rec, _ = self.svc.start_backup("loja-a", PW)
        self.svc.wait(rec["id"])
        (cdbs.SITES / "loja-a" / "index.html").write_bytes(b"versao-atual")
        seen = []

        def during(name):
            for path in ("/loja-a/", "/loja-a/extra.html", "/_api/loja-a/products.json"):
                status, headers, body = request(self.urls["public"] + path)
                seen.append((name, path, status, headers.get("retry-after"), body[:12]))
            status, _, _ = request(self.urls["public"] + "/loja-b/")                     # outra loja não é afetada
            seen.append((name, "/loja-b/", status, None, b""))
        self.svc.hooks["during_apply"] = during
        summary = self.svc.restore_summary("loja-a", rec["id"])
        op, _ = self.svc.start_restore("loja-a", rec["id"], summary["confirm_token"], "loja-a", PW)
        self.svc.wait(op["id"])
        self.assertEqual(self.svc.get("loja-a", op["id"])["state"], "CONCLUIDO")
        mine = [s for s in seen if s[1] != "/loja-b/"]
        self.assertTrue(mine)
        for name, path, status, retry, body in mine:
            self.assertEqual((status, retry), (503, "5"), (name, path))
            self.assertNotIn(b"versao-atual", body)
            self.assertNotIn(b"<!doctype", body)
        self.assertTrue(all(s[2] == 404 for s in seen if s[1] == "/loja-b/"))
        self.assertEqual(self.public("/loja-a/"), 200)                                  # terminou: volta ao normal
        status, _, body = request(self.urls["public"] + "/loja-a/")
        self.assertEqual(body, INDEX)


if __name__ == "__main__":
    unittest.main()
