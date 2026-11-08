"""Testes do formato de backup protegido (CDBK v1). Parâmetros de scrypt reduzidos só nos testes, para ficarem rápidos."""
import hashlib
import json
import os
import sys
import tempfile
import unicodedata
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import backup_crypto as bc
from backup_crypto import CryptoError, KdfParams

FAST = KdfParams(10, 8, 1)
PASSWORD = "uma senha bem longa 2026!"
CHUNK = 1 << bc.CHUNK_LOG2


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def make(self, data: bytes, name="x") -> tuple[Path, Path]:
        plain, enc = self.dir / f"{name}.bin", self.dir / f"{name}.cdbk"
        plain.write_bytes(data)
        bc.encrypt_file(plain, enc, PASSWORD, FAST)
        return plain, enc

    def code_of(self, enc: Path, password=PASSWORD) -> str:
        with self.assertRaises(CryptoError) as ctx:
            bc.decrypt_stream(enc, password, lambda b: None)
        return ctx.exception.code


class RoundTrip(Base):
    def test_sizes_around_the_chunk_boundary(self):
        for size in (0, 1, 100, CHUNK - 1, CHUNK, CHUNK + 1, 2 * CHUNK, 2 * CHUNK + 7):
            with self.subTest(size=size):
                data = os.urandom(size)
                plain, enc = self.make(data, f"s{size}")
                out = self.dir / f"o{size}.bin"
                self.assertEqual(bc.decrypt_file(enc, out, PASSWORD), size)
                self.assertEqual(out.read_bytes(), data)
                self.assertEqual(bc.verify_file(enc, PASSWORD), (size, hashlib.sha256(data).hexdigest()))
                self.assertEqual(enc.stat().st_size, 32 + size + 16 * max(1, -(-size // CHUNK)))

    def test_file_mode_and_header(self):
        _, enc = self.make(b"conteudo")
        self.assertEqual(oct(enc.stat().st_mode & 0o777), "0o600")
        info = bc.read_header(enc)
        self.assertEqual((info["format"], info["version"], info["cipher"]), ("CDBK", 1, "AES-256-GCM"))
        self.assertIn("scrypt", info["kdf"])
        self.assertIsNone(bc.read_header(self.dir / "x.bin"))
        self.assertTrue(bc.looks_encrypted(enc.read_bytes()[:8]))

    def test_nothing_readable_or_reusable_in_the_file(self):
        secret = b"SEGREDO-DO-SITE-" * 50
        _, enc = self.make(secret)
        blob = enc.read_bytes()
        self.assertNotIn(b"SEGREDO", blob)
        self.assertNotIn(PASSWORD.encode(), blob)
        self.assertNotIn(bc.derive_key(PASSWORD, blob[12:28], FAST), blob)           # a chave não vai no arquivo
        _, other = self.make(secret, "outro")
        self.assertNotEqual(blob[12:32], other.read_bytes()[12:32])                    # salt e nonce são únicos por arquivo
        self.assertNotEqual(blob[32:], other.read_bytes()[32:])

    def test_no_fixed_key_in_the_source(self):
        text = Path(bc.__file__).read_text(encoding="utf-8")
        self.assertNotRegex(text, r"b?['\"][0-9a-fA-F]{32,}['\"]")


class Rejections(Base):
    def test_wrong_password_is_rejected(self):
        _, enc = self.make(os.urandom(5000))
        self.assertEqual(self.code_of(enc, "outra senha completamente diferente"), "SENHA_INCORRETA")
        self.assertEqual(self.code_of(enc, ""), "SENHA_INCORRETA")
        with self.assertRaises(CryptoError):
            bc.decrypt_stream(enc, None, lambda b: None)
        self.assertEqual(self.code_of(enc, PASSWORD + " "), "SENHA_INCORRETA")

    def test_nothing_is_delivered_before_authentication(self):
        _, enc = self.make(os.urandom(3 * CHUNK))
        blob = bytearray(enc.read_bytes())
        blob[32 + CHUNK + 16 + 100] ^= 1                                               # estraga o 2º bloco
        enc.write_bytes(bytes(blob))
        delivered = []
        with self.assertRaises(CryptoError) as ctx:
            bc.decrypt_stream(enc, PASSWORD, delivered.append)
        self.assertEqual(ctx.exception.code, "ARQUIVO_ALTERADO")
        self.assertEqual(len(delivered), 1)                                            # só o bloco 1 (já autenticado) saiu

    def test_decrypt_file_leaves_no_partial_plaintext(self):
        _, enc = self.make(os.urandom(2 * CHUNK))
        blob = bytearray(enc.read_bytes())
        blob[-5] ^= 1
        enc.write_bytes(bytes(blob))
        out = self.dir / "saida.bin"
        with self.assertRaises(CryptoError):
            bc.decrypt_file(enc, out, PASSWORD)
        self.assertFalse(out.exists())

    def test_every_kind_of_tampering_is_detected(self):
        data = os.urandom(3 * CHUNK + 10)
        _, enc = self.make(data)
        good = enc.read_bytes()
        width = CHUNK + 16

        def tampered(blob: bytes) -> str:
            enc.write_bytes(blob)
            return self.code_of(enc)

        mid = bytearray(good); mid[32 + width + 5] ^= 0x80
        self.assertEqual(tampered(bytes(mid)), "ARQUIVO_ALTERADO")
        tag = bytearray(good); tag[-1] ^= 1
        self.assertEqual(tampered(bytes(tag)), "ARQUIVO_ALTERADO")
        first = bytearray(good); first[40] ^= 1
        self.assertIn(tampered(bytes(first)), ("SENHA_INCORRETA",))
        self.assertEqual(tampered(good[:-1]), "ARQUIVO_ALTERADO")                       # corta 1 byte
        self.assertEqual(tampered(good[:32 + 2 * width]), "ARQUIVO_ALTERADO")           # corta no limite de bloco (truncamento limpo)
        self.assertEqual(tampered(good + b"lixo"), "ARQUIVO_ALTERADO")                  # anexa lixo
        self.assertEqual(tampered(good + good[32:32 + width]), "ARQUIVO_ALTERADO")      # anexa um bloco válido
        blocks = [good[32 + i * width:32 + (i + 1) * width] for i in range(3)] + [good[32 + 3 * width:]]
        swapped = good[:32] + blocks[1] + blocks[0] + blocks[2] + blocks[3]
        self.assertEqual(tampered(swapped), "SENHA_INCORRETA")          # reordenar: 1º bloco já não autentica
        reorder = good[:32] + blocks[0] + blocks[2] + blocks[1] + blocks[3]
        self.assertEqual(tampered(reorder), "ARQUIVO_ALTERADO")
        dup = good[:32] + blocks[0] + blocks[0] + blocks[1] + blocks[2] + blocks[3]
        self.assertEqual(tampered(dup), "ARQUIVO_ALTERADO")
        self.assertEqual(tampered(good[:32]), "ARQUIVO_ALTERADO")                       # só o cabeçalho
        salt = bytearray(good); salt[12] ^= 1
        self.assertEqual(tampered(bytes(salt)), "SENHA_INCORRETA")                       # cabeçalho alterado muda a chave
        enc.write_bytes(good)
        self.assertEqual(bc.verify_file(enc, PASSWORD)[0], len(data))                    # e o original continua válido

    def test_bad_header_and_hostile_parameters(self):
        _, enc = self.make(b"abc")
        good = enc.read_bytes()
        for blob, code in ((b"", "FORMATO_INVALIDO"), (b"PK\x03\x04" + b"0" * 100, "FORMATO_INVALIDO"),
                           (good[:6] + bytes([2]) + good[7:], "VERSAO_NAO_SUPORTADA"),
                           (good[:7] + bytes([9]) + good[8:], "VERSAO_NAO_SUPORTADA"),
                           (good[:8] + bytes([30]) + good[9:], "PARAMETROS_INVALIDOS"),      # N=2^30 não pode pedir 128 GB de memória
                           (good[:8] + bytes([5]) + good[9:], "PARAMETROS_INVALIDOS"),
                           (good[:9] + bytes([200]) + good[10:], "PARAMETROS_INVALIDOS"),
                           (good[:10] + bytes([0]) + good[11:], "PARAMETROS_INVALIDOS"),
                           (good[:11] + bytes([40]) + good[12:], "PARAMETROS_INVALIDOS")):
            with self.subTest(code=code, head=blob[:12]):
                enc.write_bytes(blob)
                self.assertEqual(self.code_of(enc), code)
        self.assertIsNone(bc.read_header(enc))

    def test_plaintext_limit_stops_decompression_of_a_huge_file(self):
        _, enc = self.make(os.urandom(2 * CHUNK))
        with self.assertRaises(CryptoError):
            bc.decrypt_stream(enc, PASSWORD, lambda b: None, max_plain=CHUNK)

    def test_missing_library_is_reported_not_faked(self):
        plain = self.dir / "p"; plain.write_bytes(b"x")
        with patch.object(bc, "AESGCM", None):
            self.assertFalse(bc.available())
            self.assertIn("cryptography", bc.unavailable_reason())
            with self.assertRaises(CryptoError) as ctx:
                bc.encrypt_file(plain, self.dir / "e", PASSWORD, FAST)
            self.assertEqual(ctx.exception.code, "INDISPONIVEL")
            self.assertFalse((self.dir / "e").exists())


class PasswordHandling(Base):
    def test_verifier_does_not_contain_password_or_unlock_anything(self):
        verifier = bc.make_verifier(PASSWORD, FAST)
        text = json.dumps(verifier)
        self.assertNotIn(PASSWORD, text)
        self.assertTrue(bc.check_verifier(verifier, PASSWORD))
        self.assertFalse(bc.check_verifier(verifier, PASSWORD + "x"))
        self.assertFalse(bc.check_verifier({}, PASSWORD))
        self.assertFalse(bc.check_verifier({"params": {"n_log2": 99, "r": 8, "p": 1}, "salt": "00", "check": "0"}, PASSWORD))
        _, enc = self.make(b"dados")
        blob = enc.read_bytes()
        self.assertNotIn(verifier["check"].encode(), blob)                               # o selo não é a chave do arquivo
        self.assertNotEqual(bc.derive_key(PASSWORD, bytes.fromhex(verifier["salt"]), FAST).hex(), verifier["check"])

    def test_unicode_forms_give_the_same_key(self):
        composed = "Senha açaí longa 123"
        decomposed = unicodedata.normalize("NFD", composed)
        self.assertNotEqual(composed, decomposed)
        self.assertEqual(bc.derive_key(composed, b"s" * 16, FAST), bc.derive_key(decomposed, b"s" * 16, FAST))

    def test_strength_rules(self):
        bc.check_password_strength("uma senha longa o bastante")
        for bad in ("curta", "aaaaaaaaaaaaaaaa", "121212121212", "", None, 123, "x" * 300):
            with self.subTest(bad=bad), self.assertRaises(CryptoError):
                bc.check_password_strength(bad)

    def test_default_parameters_are_the_documented_ones(self):
        self.assertEqual(KdfParams().describe(), "scrypt N=2^15, r=8, p=1")
        self.assertTrue(bc.available(), bc.unavailable_reason())


if __name__ == "__main__":
    unittest.main()
