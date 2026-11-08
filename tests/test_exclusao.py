"""Evolução 2.1 — Correção B: espaço, exclusão controlada de backups e política de retenção (sempre manual). Sem rede."""
import json
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import backup
import cdbs
from backup import BackupError
import test_backup as tb
from test_backup import DAY, INDEX, PW, env


def files_of(e, slug="loja-a"):
    return sorted(p.name for p in (e.root / "backups" / slug).iterdir() if p.suffix in (".cdbk", ".zip"))


def delete(e, slug, rid, text=None, last=False, summary=None):
    summary = summary or e.svc.delete_summary(slug, rid)
    return e.svc.delete_backup(slug, rid, summary["confirm_token"], text if text is not None else summary["confirm_text"], last)


class DeleteTests(unittest.TestCase):
    def test_confirmed_delete_removes_only_that_backup_and_is_logged_without_secrets(self):
        with env() as e:
            e.site()
            password = e.store()
            keep = e.backup(plain=False)
            target = e.backup(plain=False)
            site_before, catalog_before = e.snapshot(), (e.cats / "loja-a" / "store.json").read_bytes()
            summary = e.svc.delete_summary("loja-a", target["id"])
            self.assertEqual(summary["confirm_text"], target["id"][:8])              # o aviso identifica qual backup é
            self.assertFalse(summary["is_last_validated"])
            self.assertIn("APAGADO", summary["warning"])
            result = delete(e, "loja-a", target["id"], summary=summary)
            self.assertEqual(result["state"], "EXCLUIDO")
            self.assertEqual(files_of(e), [f"{keep['id']}.cdbk"])
            self.assertEqual([r["id"] for r in e.svc.list("loja-a")["backups"]], [keep["id"]])    # some da lista
            self.assertEqual(e.snapshot(), site_before)                              # site e catálogo intactos
            self.assertEqual((e.cats / "loja-a" / "store.json").read_bytes(), catalog_before)
            events = json.dumps(e.svc.events(limit=100))
            self.assertIn(target["id"][:8], events)                                  # registra id, resultado e data
            self.assertIn("exclusao", events)
            for secret in (PW, password):
                self.assertNotIn(secret, events)
            self.assertNotIn(str(e.root), events)
            self.assertEqual(e.svc.get("loja-a", keep["id"])["state"], "CONCLUIDO")

    def test_wrong_confirmation_deletes_nothing(self):
        with env() as e:
            e.site()
            a, b = e.backup(), e.backup()
            summary = e.svc.delete_summary("loja-a", a["id"])
            for text in (None, "", "APAGAR", b["id"][:8], a["id"][:7], 123):
                with self.subTest(text=text), self.assertRaises(BackupError) as ctx:
                    e.svc.delete_backup("loja-a", a["id"], summary["confirm_token"], text)
                self.assertEqual(ctx.exception.code, "CONFIRMACAO")
            for token in (None, "", "falso", 5):
                with self.subTest(token=token), self.assertRaises(BackupError) as ctx:
                    e.svc.delete_backup("loja-a", a["id"], token, a["id"][:8])
                self.assertEqual(ctx.exception.code, "CONFIRMACAO")
            self.assertEqual(len(files_of(e)), 2)

    def test_token_is_single_use_expires_and_is_bound_to_that_backup(self):
        with env() as e:
            e.site()
            a, b = e.backup(), e.backup()
            summary = e.svc.delete_summary("loja-a", a["id"])
            with self.assertRaises(BackupError):                                      # o código de A não apaga B
                e.svc.delete_backup("loja-a", b["id"], summary["confirm_token"], b["id"][:8])
            e.clock.t += backup.CONFIRM_TTL + 1
            with self.assertRaises(BackupError):                                      # expirou
                e.svc.delete_backup("loja-a", a["id"], summary["confirm_token"], a["id"][:8])
            self.assertEqual(len(files_of(e)), 2)
            summary = e.svc.delete_summary("loja-a", a["id"])
            e.svc.delete_backup("loja-a", a["id"], summary["confirm_token"], a["id"][:8])
            with self.assertRaises(BackupError):                                      # uso único
                e.svc.delete_backup("loja-a", a["id"], summary["confirm_token"], a["id"][:8])

    def test_last_validated_backup_needs_an_extra_explicit_confirmation(self):
        with env() as e:
            e.site()
            only = e.backup(plain=False)
            summary = e.svc.delete_summary("loja-a", only["id"])
            self.assertTrue(summary["is_last_validated"])
            self.assertIn("ÚLTIMO", summary["last_warning"])
            with self.assertRaises(BackupError) as ctx:
                e.svc.delete_backup("loja-a", only["id"], summary["confirm_token"], summary["confirm_text"], False)
            self.assertEqual(ctx.exception.code, "ULTIMO_VALIDADO")
            self.assertEqual(files_of(e), [f"{only['id']}.cdbk"])
            with self.assertRaises(BackupError):                                      # "truthy" não vale: só True explícito
                e.svc.delete_backup("loja-a", only["id"], summary["confirm_token"], summary["confirm_text"], "sim")
            self.assertEqual(files_of(e), [f"{only['id']}.cdbk"])
            result = e.svc.delete_backup("loja-a", only["id"], summary["confirm_token"], summary["confirm_text"], True)
            self.assertEqual(result["state"], "EXCLUIDO")
            self.assertEqual(files_of(e), [])
            self.assertEqual((e.sites / "loja-a" / "index.html").read_bytes(), INDEX)  # o site continua lá

    def test_invalid_backups_do_not_count_as_the_last_validated(self):
        with env() as e:
            e.site()
            good, bad = e.backup(), e.backup()
            path = e.root / "backups" / "loja-a" / f"{bad['id']}.zip"
            path.write_bytes(path.read_bytes()[:50])
            e.svc.validate("loja-a", bad["id"])
            self.assertEqual(e.svc.get("loja-a", bad["id"])["state"], "INVALIDO")
            summary = e.svc.delete_summary("loja-a", bad["id"])
            self.assertFalse(summary["is_last_validated"])
            self.assertEqual(delete(e, "loja-a", bad["id"], summary=summary)["state"], "EXCLUIDO")
            self.assertTrue(e.svc.delete_summary("loja-a", good["id"])["is_last_validated"])

    def test_blocked_while_an_operation_is_active(self):
        with env() as e:
            e.site()
            old = e.backup()
            gate, entered = threading.Event(), threading.Event()
            e.svc.hooks["during_create"] = lambda n: (entered.set(), gate.wait(10))
            running, _ = e.svc.start_backup("loja-a", PW)
            entered.wait(5)
            with self.assertRaises(BackupError) as ctx:
                e.svc.delete_summary("loja-a", old["id"])
            self.assertEqual(ctx.exception.code, "BACKUP_EM_USO")
            gate.set()
            e.svc.wait(running["id"])
            self.assertEqual(len(files_of(e)), 2)
            e.svc.hooks.clear()

    def test_a_backup_cannot_be_deleted_through_another_store(self):
        with env() as e:
            e.site("loja-a")
            e.site("loja-b")
            a = e.backup("loja-a")
            e.backup("loja-b")
            for slug in ("loja-b", "nao-existe"):
                with self.subTest(slug=slug), self.assertRaises(BackupError):
                    e.svc.delete_summary(slug, a["id"])
            summary = e.svc.delete_summary("loja-a", a["id"])
            with self.assertRaises(BackupError):
                e.svc.delete_backup("loja-b", a["id"], summary["confirm_token"], summary["confirm_text"], True)
            self.assertEqual(len(files_of(e, "loja-a")), 1)

    def test_only_finished_backups_can_be_deleted(self):
        with env() as e:
            e.site()
            e.backup()
            e.free[0] = 10
            failed = e.backup()
            self.assertEqual(failed["state"], "FALHOU")
            with self.assertRaises(BackupError) as ctx:
                e.svc.delete_summary("loja-a", failed["id"])
            self.assertEqual(ctx.exception.code, "EXCLUSAO_INVALIDA")

    def test_failure_to_unlink_keeps_the_backup_listed_and_available(self):
        with env() as e:
            e.site()
            e.backup()
            target = e.backup()
            summary = e.svc.delete_summary("loja-a", target["id"])
            with patch.object(Path, "unlink", side_effect=OSError("somente leitura")):
                with self.assertRaises(BackupError):
                    e.svc.delete_backup("loja-a", target["id"], summary["confirm_token"], summary["confirm_text"])
            self.assertEqual(e.svc.get("loja-a", target["id"])["state"], "CONCLUIDO")
            self.assertEqual(len(files_of(e)), 2)
            self.assertTrue(any(ev["kind"] == "exclusao_falhou" for ev in e.svc.events(limit=100)))
            self.assertEqual(e.svc.validate("loja-a", target["id"])["state"], "CONCLUIDO")       # continua íntegro e restaurável


class RetentionTests(unittest.TestCase):
    def many(self, e, count=5, plain=True):
        e.site()
        ids = []
        for _ in range(count):
            e.clock.t += 10 * DAY
            ids.append(e.backup(plain=plain)["id"])
        return ids                                                                    # do mais antigo ao mais novo

    def test_default_keeps_everything_and_auto_delete_is_disabled(self):
        with env() as e:
            ids = self.many(e)
            policy = e.svc.retention_get()
            self.assertEqual(policy["mode"], "off")
            self.assertFalse(policy["auto_enabled"])
            self.assertIn("BLOCKED", policy["auto_note"])
            self.assertTrue(all("impact" in o for o in policy["options"]))            # as opções mostram o impacto
            preview = e.svc.retention_preview()
            self.assertEqual(preview["candidates"], [])
            self.assertEqual(len(files_of(e)), len(ids))

    def test_policy_validation(self):
        with env() as e:
            for mode, last, days in (("auto", None, None), (None, None, None), ("keep_last", 0, None), ("keep_last", -3, None), ("keep_last", True, None),
                                     ("keep_last", "5", None), ("keep_days", None, 1), ("keep_days", None, 99999), ("keep_days", None, 1.5)):
                with self.subTest(mode=mode, last=last, days=days), self.assertRaises(BackupError) as ctx:
                    e.svc.retention_set(mode, last, days)
                self.assertEqual(ctx.exception.code, "POLITICA_INVALIDA")
            self.assertEqual(e.svc.retention_set("keep_last", 3, None)["keep_last"], 3)
            self.assertFalse(e.svc.retention_get()["auto_enabled"])                    # nenhuma combinação liga a execução automática

    def test_keep_last_preview_lists_only_the_older_ones_and_deletes_nothing(self):
        with env() as e:
            ids = self.many(e, 5)
            e.svc.retention_set("keep_last", 2, None)
            preview = e.svc.retention_preview()
            self.assertEqual({c["id"] for c in preview["candidates"]}, set(ids[:3]))   # os 3 mais antigos
            self.assertTrue(all(c["reason"] and c["short_id"] and c["size_bytes"] > 0 for c in preview["candidates"]))
            self.assertEqual(preview["total_bytes"], sum(c["size_bytes"] for c in preview["candidates"]))
            self.assertEqual(len(files_of(e)), 5)                                      # a prévia não apaga nada

    def test_keep_days_and_the_newest_validated_is_never_a_candidate(self):
        with env() as e:
            ids = self.many(e, 3)
            e.clock.t += 400 * DAY                                                     # todos ficam "velhos"
            e.svc.retention_set("keep_days", None, 30)
            preview = e.svc.retention_preview()
            self.assertEqual({c["id"] for c in preview["candidates"]}, set(ids[:2]))   # o mais novo (último validado) fica
        with env() as e:
            e.site()
            only = e.backup()
            e.clock.t += 1000 * DAY
            e.svc.retention_set("keep_days", None, 7)
            self.assertEqual(e.svc.retention_preview()["candidates"], [])              # único backup: nunca entra

    def test_apply_needs_the_exact_word_and_a_valid_preview_and_deletes_only_what_was_previewed(self):
        with env() as e:
            ids = self.many(e, 5)
            e.svc.retention_set("keep_last", 2, None)
            preview = e.svc.retention_preview()
            for token, text in ((preview["confirm_token"], "apagar"), (preview["confirm_token"], ""), ("falso", "APAGAR"), (None, "APAGAR")):
                with self.subTest(text=text), self.assertRaises(BackupError) as ctx:
                    e.svc.retention_apply(token, text)
                self.assertEqual(ctx.exception.code, "CONFIRMACAO")
            self.assertEqual(len(files_of(e)), 5)
            e.clock.t += 5
            newest = e.backup()["id"]                                                  # nasce DEPOIS da prévia
            result = e.svc.retention_apply(preview["confirm_token"], "APAGAR")
            self.assertEqual({d["short_id"] for d in result["deleted"]}, {i[:8] for i in ids[:3]})
            self.assertGreater(result["freed_bytes"], 0)
            remaining = {r["id"] for r in e.svc.list("loja-a")["backups"]}
            self.assertEqual(remaining, {ids[3], ids[4], newest})                      # o novo e os mais recentes ficam
            with self.assertRaises(BackupError):                                       # uso único
                e.svc.retention_apply(preview["confirm_token"], "APAGAR")

    def test_apply_skips_stores_with_an_active_operation(self):
        with env() as e:
            ids = self.many(e, 4)
            e.svc.retention_set("keep_last", 1, None)
            preview = e.svc.retention_preview()
            gate, entered = threading.Event(), threading.Event()
            e.svc.hooks["during_create"] = lambda n: (entered.set(), gate.wait(10))
            running, _ = e.svc.start_backup("loja-a", PW)
            entered.wait(5)
            result = e.svc.retention_apply(preview["confirm_token"], "APAGAR")
            self.assertEqual(result["deleted"], [])
            self.assertEqual(len(result["skipped"]), 3)
            gate.set()
            e.svc.wait(running["id"])
            self.assertEqual(len(files_of(e)), 5)

    def test_nothing_is_ever_deleted_automatically(self):
        """Mesmo com política ativa, espaço acabando, reinício, lembrete e restauração: nenhum arquivo de backup some sozinho."""
        with env() as e:
            ids = self.many(e, 4)
            e.svc.retention_set("keep_last", 1, None)
            before = files_of(e)
            e.free[0] = 1024                                                           # sem espaço: não apaga para abrir lugar
            self.assertEqual(e.backup(plain=False)["state"], "FALHOU")
            e.free[0] = 10 ** 12
            e.clock.t += 1000 * DAY
            e.svc.reminder_tick()
            e.svc.overview()
            e.svc.list("loja-a")
            e.svc.recover()
            e.svc.retention_preview()
            self.assertEqual(files_of(e), before)
            new, _ = e.restore("loja-a", ids[0])
            self.assertEqual(new["state"], "CONCLUIDO", new)
            self.assertTrue(set(before) <= set(files_of(e)))                           # só ganhou o backup de segurança


class SpaceViewTests(unittest.TestCase):
    def test_sites_catalogs_and_backups_are_reported_separately_with_free_space(self):
        with env() as e:
            e.site()
            e.store(products=2)
            e.backup(plain=False)
            e.backup(plain=True)
            e.free[0] = 123_456_789
            total = e.svc.storage()
            one = e.svc.storage("loja-a")
            for data in (total, one):
                self.assertEqual(data["free_bytes"], 123_456_789)
                self.assertGreater(data["backup_bytes"], 0)
            self.assertGreater(total["sites_bytes"], 0)
            self.assertGreater(total["catalogs_bytes"], 0)
            self.assertEqual((one["encrypted_count"], one["legacy_count"], one["backup_count"]), (1, 1, 2))
            self.assertEqual(total["sites_bytes"], one["site_bytes"])
            self.assertEqual(total["catalogs_bytes"], one["catalog_bytes"])
            self.assertEqual(e.svc.overview()["storage"]["backup_count"], 2)

    def test_deleting_updates_the_numbers(self):
        with env() as e:
            e.site()
            a, b = e.backup(), e.backup()
            before = e.svc.storage("loja-a")
            delete(e, "loja-a", a["id"])
            after = e.svc.storage("loja-a")
            self.assertEqual(after["backup_count"], before["backup_count"] - 1)
            self.assertLess(after["backup_bytes"], before["backup_bytes"])
            self.assertEqual(after["site_bytes"], before["site_bytes"])


class DeleteOverHttpTests(tb.HttpTests):
    """Rotas de exclusão/retenção: só o administrador logado, com origem e cabeçalho, e nunca no servidor público."""

    def make(self, call, count=2):
        ids = []
        for _ in range(count):
            _, created = call("POST", "/api/backups/loja-a/create", {"password": PW})
            rid = created["backup"]["id"]
            self.wait(call, f"/api/backups/loja-a/{rid}", {"CONCLUIDO"})
            ids.append(rid)
        return ids

    def test_delete_flow_and_authorization(self):
        with self.admin() as (urls, call, raw_get, headers):
            a, b = self.make(call)
            status, summary = call("POST", f"/api/backups/loja-a/{a}/delete/summary", {})
            self.assertEqual(status, 200)
            self.assertNotIn(str(cdbs.DATA), json.dumps(summary))
            token, text = summary["summary"]["confirm_token"], summary["summary"]["confirm_text"]
            admin = urls["admin"]
            # visitante (sem sessão) e pedido sem Origin / sem cabeçalho / de outro site não apagam
            from test_catalog import request
            for hdrs, expected in (({"Origin": admin}, 401), ({"Origin": admin, "Cookie": headers["Cookie"]}, 403),
                                   ({"Origin": "http://evil.example", "Cookie": headers["Cookie"], "X-CDBS-Request": "1"}, 403), ({"Cookie": headers["Cookie"], "X-CDBS-Request": "1"}, 403)):
                status, _, _ = request(admin + f"/api/backups/loja-a/{a}/delete", "POST", json_body={"confirm_token": token, "confirm_text": text}, headers=hdrs)
                self.assertEqual(status, expected, hdrs)
            self.assertTrue((cdbs.DATA / "backups" / "loja-a" / f"{a}.cdbk").exists())
            status, _ = call("POST", f"/api/backups/loja-a/{a}/delete", {"confirm_token": token, "confirm_text": "errado"})
            self.assertEqual(status, 403)
            status, done = call("POST", f"/api/backups/loja-a/{a}/delete", {"confirm_token": token, "confirm_text": text})
            self.assertEqual((status, done["backup"]["state"]), (200, "EXCLUIDO"))
            self.assertFalse((cdbs.DATA / "backups" / "loja-a" / f"{a}.cdbk").exists())
            self.assertEqual(call("GET", f"/api/backups/loja-a/{a}/download")[0], 404)           # excluído não baixa
            self.assertEqual([r["id"] for r in call("GET", "/api/backups/loja-a")[1]["backups"]], [b])
            # outra loja não alcança o backup
            status, _ = call("POST", f"/api/backups/loja-b/{b}/delete/summary", {})
            self.assertEqual(status, 404)
            # o último validado pede a confirmação extra
            status, summary = call("POST", f"/api/backups/loja-a/{b}/delete/summary", {})
            self.assertTrue(summary["summary"]["is_last_validated"])
            status, body = call("POST", f"/api/backups/loja-a/{b}/delete", {"confirm_token": summary["summary"]["confirm_token"], "confirm_text": summary["summary"]["confirm_text"]})
            self.assertEqual((status, body["code"]), (409, "ULTIMO_VALIDADO"))
            status, body = call("POST", f"/api/backups/loja-a/{b}/delete", {"confirm_token": summary["summary"]["confirm_token"], "confirm_text": summary["summary"]["confirm_text"], "confirm_last": True})
            self.assertEqual(status, 200)

    def test_public_server_has_no_backup_or_delete_route(self):
        with self.admin() as (urls, call, raw_get, headers):
            (a,) = self.make(call, 1)
            from test_catalog import request
            for method in ("POST", "DELETE", "PUT", "GET"):
                for path in (f"/api/backups/loja-a/{a}/delete", f"/api/backups/loja-a/{a}", "/api/backups/_retencao/apply", f"/_painel/api/backups/loja-a/{a}/delete"):
                    status, _, _ = request(urls["public"] + path, method, json_body={}, headers={"Origin": urls["public"], "X-CDBS-Request": "1"})
                    self.assertIn(status, (401, 403, 404, 405), (method, path))
            self.assertTrue((cdbs.DATA / "backups" / "loja-a" / f"{a}.cdbk").exists())

    def test_retention_routes(self):
        with self.admin() as (urls, call, raw_get, headers):
            self.make(call, 3)
            status, data = call("GET", "/api/backups/_retencao")
            self.assertEqual((status, data["policy"]["mode"], data["policy"]["auto_enabled"]), (200, "off", False))
            self.assertEqual(call("POST", "/api/backups/_retencao", {"mode": "automatico"})[0], 400)
            status, data = call("POST", "/api/backups/_retencao", {"mode": "keep_last", "keep_last": 1})
            self.assertEqual((status, data["policy"]["keep_last"], data["policy"]["auto_enabled"]), (200, 1, False))
            status, prev = call("POST", "/api/backups/_retencao/preview", {})
            self.assertEqual(len(prev["preview"]["candidates"]), 2)
            self.assertEqual(call("POST", "/api/backups/_retencao/apply", {"confirm_token": prev["preview"]["confirm_token"], "confirm_text": "nao"})[0], 403)
            status, res = call("POST", "/api/backups/_retencao/apply", {"confirm_token": prev["preview"]["confirm_token"], "confirm_text": "APAGAR"})
            self.assertEqual((status, len(res["result"]["deleted"])), (200, 2))
            self.assertEqual(len(call("GET", "/api/backups/loja-a")[1]["backups"]), 1)


# A subclasse acima herdaria os testes de HttpTests e os repetiria; mantemos só os métodos novos nela.
for _name in [n for n in dir(tb.HttpTests) if n.startswith("test_")]:
    setattr(DeleteOverHttpTests, _name, None)
del _name

if __name__ == "__main__":
    unittest.main()
