"""CDBS.OS — criptografia dos backups (formato CDBK v1).

Nada aqui inventa algoritmo. As peças são:
  • cifra autenticada: AES-256-GCM, da biblioteca `cryptography` (reconhecida e mantida);
  • derivação de chave a partir da senha: scrypt, da biblioteca padrão (`hashlib.scrypt`);
  • aleatoriedade: `secrets` / `os.urandom`.
A única coisa "nossa" é o enquadramento em blocos (construção STREAM, a mesma ideia de Tink/age), necessário
porque um backup pode ter centenas de MB e o AES-GCM comum exige o arquivo inteiro na memória.

Formato (tudo big-endian), documentado em docs/CRIPTOGRAFIA.md:

    cabeçalho (32 bytes)
      0..5    "CDBSBK"                 magia
      6       versão do formato (1)
      7       KDF (1 = scrypt)
      8       log2(N) do scrypt
      9       r do scrypt
      10      p do scrypt
      11      log2(tamanho do bloco em claro)   (20 = 1 MiB)
      12..27  salt (16 bytes aleatórios, único por arquivo)
      28..31  prefixo do nonce (4 bytes aleatórios, único por arquivo)
    blocos, um após o outro, até o fim do arquivo
      texto cifrado + tag GCM de 16 bytes (cada bloco em claro tem 2^k bytes, exceto o último)
      nonce  = prefixo(4) + contador do bloco(8)
      AAD    = cabeçalho(32) + contador(8) + marcador "último bloco"(1: 0 ou 1)

O AAD amarra cada bloco ao cabeçalho, à posição e ao fato de ser o último: reordenar, duplicar, cortar o fim
ou anexar lixo faz a autenticação falhar. A chave NUNCA vai dentro do arquivo e não há chave fixa no código.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

try:                                                      # a Central funciona sem isto, só não cria backups novos
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    _IMPORT_ERROR = ""
except Exception as exc:                                  # noqa: BLE001
    AESGCM = None                                         # type: ignore[assignment]
    InvalidTag = Exception                                # type: ignore[assignment,misc]
    _IMPORT_ERROR = type(exc).__name__

MAGIC = b"CDBSBK"
VERSION = 1
KDF_SCRYPT = 1
HEADER_LEN = 32
TAG_LEN = 16
CHUNK_LOG2 = 20                                           # blocos de 1 MiB
FILE_EXTENSION = ".cdbk"
CIPHER_NAME = "AES-256-GCM"
CHECK_LABEL = b"cdbs-backup-password-check-v1"

DEFAULT_N_LOG2, DEFAULT_R, DEFAULT_P = 15, 8, 1           # 32 MiB de memória: cabe em celulares modestos
MIN_N_LOG2, MAX_N_LOG2 = 10, 18                           # limites aceitos AO LER (um arquivo hostil não pode pedir memória sem fim)
MAX_SCRYPT_MEMORY = 256 * 1024 * 1024
MIN_PASSWORD_CHARS = 12


class CryptoError(Exception):
    """Erro de criptografia com código estável; a mensagem nunca contém senha, chave nem caminho."""

    MESSAGES = {
        "INDISPONIVEL": "A biblioteca de criptografia não está instalada neste aparelho.",
        "FORMATO_INVALIDO": "O arquivo não está no formato de backup protegido do CDBS.",
        "VERSAO_NAO_SUPORTADA": "Este backup protegido foi feito por uma versão do formato que este CDBS não conhece.",
        "SENHA_INCORRETA": "Senha incorreta, ou o arquivo foi alterado.",
        "ARQUIVO_ALTERADO": "O arquivo protegido foi alterado, cortado ou está corrompido.",
        "PARAMETROS_INVALIDOS": "Os parâmetros de proteção do arquivo estão fora do limite seguro.",
        "SENHA_FRACA": f"Use uma senha de pelo menos {MIN_PASSWORD_CHARS} caracteres.",
    }

    def __init__(self, code: str):
        super().__init__(self.MESSAGES.get(code, "Erro de criptografia."))
        self.code = code


@dataclass(frozen=True)
class KdfParams:
    n_log2: int = DEFAULT_N_LOG2
    r: int = DEFAULT_R
    p: int = DEFAULT_P

    def check(self) -> "KdfParams":
        if not (MIN_N_LOG2 <= self.n_log2 <= MAX_N_LOG2 and 1 <= self.r <= 16 and 1 <= self.p <= 4):
            raise CryptoError("PARAMETROS_INVALIDOS")
        if 128 * (1 << self.n_log2) * self.r > MAX_SCRYPT_MEMORY:
            raise CryptoError("PARAMETROS_INVALIDOS")
        return self

    def describe(self) -> str:
        return f"scrypt N=2^{self.n_log2}, r={self.r}, p={self.p}"

    def as_dict(self) -> dict:
        return {"n_log2": self.n_log2, "r": self.r, "p": self.p}


def available() -> bool:
    return AESGCM is not None and hasattr(hashlib, "scrypt")


def unavailable_reason() -> str:
    if AESGCM is None:
        return f"pacote 'cryptography' ausente ({_IMPORT_ERROR})"
    if not hasattr(hashlib, "scrypt"):
        return "este Python não tem hashlib.scrypt"
    return ""


def normalize_password(password: object) -> bytes:
    """NFC + UTF-8: a mesma senha digitada em teclados diferentes gera a mesma chave."""
    if not isinstance(password, str):
        raise CryptoError("SENHA_INCORRETA")
    return unicodedata.normalize("NFC", password).encode("utf-8")


def check_password_strength(password: object) -> None:
    if not isinstance(password, str) or len(unicodedata.normalize("NFC", password)) < MIN_PASSWORD_CHARS or len(password) > 200:
        raise CryptoError("SENHA_FRACA")
    if len(set(password)) < 4:                           # "aaaaaaaaaaaa", "121212121212"…
        raise CryptoError("SENHA_FRACA")


def derive_key(password: object, salt: bytes, params: KdfParams) -> bytes:
    if not available():
        raise CryptoError("INDISPONIVEL")
    params.check()
    return hashlib.scrypt(normalize_password(password), salt=salt, n=1 << params.n_log2, r=params.r, p=params.p,
                          maxmem=MAX_SCRYPT_MEMORY + 8 * 1024 * 1024, dklen=32)


# ----------------------------------------------------------------------- verificador da senha (não é a senha)

def make_verifier(password: object, params: KdfParams) -> dict:
    """Guarda só um 'selo' para detectar erro de digitação depois. Não permite recuperar a senha nem descriptografar nada."""
    salt = secrets.token_bytes(16)
    key = derive_key(password, salt, params)
    return {"version": 1, "kdf": "scrypt", "params": params.as_dict(), "salt": salt.hex(),
            "check": hmac.new(key, CHECK_LABEL, hashlib.sha256).hexdigest()}


def check_verifier(verifier: dict, password: object) -> bool:
    try:
        params = KdfParams(**{k: int(v) for k, v in verifier["params"].items()}).check()
        key = derive_key(password, bytes.fromhex(verifier["salt"]), params)
        return hmac.compare_digest(hmac.new(key, CHECK_LABEL, hashlib.sha256).hexdigest(), str(verifier["check"]))
    except (KeyError, TypeError, ValueError, AttributeError, CryptoError):
        return False


# ----------------------------------------------------------------------- cabeçalho e blocos

def _header(params: KdfParams, salt: bytes, prefix: bytes) -> bytes:
    return MAGIC + bytes([VERSION, KDF_SCRYPT, params.n_log2, params.r, params.p, CHUNK_LOG2]) + salt + prefix


def parse_header(raw: bytes) -> tuple[KdfParams, bytes, bytes, int]:
    if len(raw) < HEADER_LEN or raw[:6] != MAGIC:
        raise CryptoError("FORMATO_INVALIDO")
    if raw[6] != VERSION or raw[7] != KDF_SCRYPT:
        raise CryptoError("VERSAO_NAO_SUPORTADA")
    params = KdfParams(raw[8], raw[9], raw[10]).check()
    chunk_log2 = raw[11]
    if not 12 <= chunk_log2 <= 24:
        raise CryptoError("PARAMETROS_INVALIDOS")
    return params, raw[12:28], raw[28:32], 1 << chunk_log2


def read_header(path: Path) -> dict | None:
    """Lê só o cabeçalho (sem senha). Devolve None se não for um arquivo CDBK."""
    try:
        with open(path, "rb") as handle:
            raw = handle.read(HEADER_LEN)
        params, _salt, _prefix, chunk = parse_header(raw)
    except (OSError, CryptoError):
        return None
    return {"format": "CDBK", "version": VERSION, "cipher": CIPHER_NAME, "kdf": params.describe(), "kdf_params": params.as_dict(), "chunk_bytes": chunk}


def looks_encrypted(first_bytes: bytes) -> bool:
    return first_bytes[:6] == MAGIC


def _nonce(prefix: bytes, counter: int) -> bytes:
    return prefix + counter.to_bytes(8, "big")


def _aad(header: bytes, counter: int, final: bool) -> bytes:
    return header + counter.to_bytes(8, "big") + (b"\x01" if final else b"\x00")


def encrypt_file(src: Path, dst: Path, password: object, params: KdfParams | None = None,
                 progress: Callable[[int], None] | None = None) -> dict:
    """Cifra `src` em `dst` (modo 0600, fsync). Devolve o resumo público do formato. `dst` não pode existir."""
    if not available():
        raise CryptoError("INDISPONIVEL")
    params = (params or KdfParams()).check()
    salt, prefix = secrets.token_bytes(16), secrets.token_bytes(4)
    header = _header(params, salt, prefix)
    aead = AESGCM(derive_key(password, salt, params))
    chunk = 1 << CHUNK_LOG2
    fd = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as out, open(src, "rb") as source:
        out.write(header)
        counter, current = 0, source.read(chunk)
        while True:
            following = source.read(chunk) if len(current) == chunk else b""
            final = not following
            out.write(aead.encrypt(_nonce(prefix, counter), current, _aad(header, counter, final)))
            if progress:
                progress(counter)
            if final:
                break
            counter, current = counter + 1, following
        out.flush()
        os.fsync(out.fileno())
    return {"format": "CDBK", "version": VERSION, "cipher": CIPHER_NAME, "kdf": params.describe(), "kdf_params": params.as_dict()}


def decrypt_stream(src: Path, password: object, sink: Callable[[bytes], None], *, max_plain: int | None = None) -> int:
    """Descriptografa `src` bloco a bloco entregando o texto em claro a `sink`. Cada bloco é autenticado ANTES de ser entregue.
    Falha com SENHA_INCORRETA (1º bloco), ARQUIVO_ALTERADO (qualquer outro), FORMATO_INVALIDO/VERSAO_NAO_SUPORTADA."""
    if not available():
        raise CryptoError("INDISPONIVEL")
    total = 0
    try:
        with open(src, "rb") as handle:
            header = handle.read(HEADER_LEN)
            params, salt, prefix, chunk = parse_header(header)
            aead = AESGCM(derive_key(password, salt, params))
            width = chunk + TAG_LEN
            counter, current = 0, handle.read(width)
            if len(current) < TAG_LEN:
                raise CryptoError("ARQUIVO_ALTERADO")
            while True:
                following = handle.read(width) if len(current) == width else b""
                final = not following
                try:
                    plain = aead.decrypt(_nonce(prefix, counter), current, _aad(header, counter, final))
                except InvalidTag:
                    raise CryptoError("SENHA_INCORRETA" if counter == 0 else "ARQUIVO_ALTERADO") from None
                total += len(plain)
                if max_plain is not None and total > max_plain:
                    raise CryptoError("ARQUIVO_ALTERADO")
                sink(plain)
                if final:
                    break
                counter, current = counter + 1, following
                if len(current) < TAG_LEN:
                    raise CryptoError("ARQUIVO_ALTERADO")
    except OSError:
        raise CryptoError("ARQUIVO_ALTERADO") from None
    return total


def decrypt_file(src: Path, dst: Path, password: object, *, max_plain: int | None = None) -> int:
    """Descriptografa para `dst` (0600). Se falhar, `dst` é apagado: nunca sobra texto em claro parcial."""
    fd = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as out:
            total = decrypt_stream(src, password, out.write, max_plain=max_plain)
            out.flush()
            os.fsync(out.fileno())
        return total
    except BaseException:
        Path(dst).unlink(missing_ok=True)
        raise


def verify_file(src: Path, password: object) -> tuple[int, str]:
    """Autentica o arquivo inteiro sem gravar nada: devolve (bytes em claro, SHA-256 do texto em claro)."""
    digest = hashlib.sha256()
    total = decrypt_stream(src, password, digest.update)
    return total, digest.hexdigest()
