"""CDBS.OS — backups por loja, criptografia, restauração segura, exclusão controlada e lembrete mensal.

Um backup NOVO é UM arquivo .cdbk: um ZIP com manifest.json (loja, data, versão do formato e, para cada arquivo,
tamanho e SHA-256) criptografado com AES-256-GCM (backup_crypto.py; senha de recuperação do administrador, derivada
por scrypt e nunca gravada). Backups da Evolução 2 são .zip sem criptografia e aparecem como LEGADO.
Ficam em data/backups/<loja>/ (pasta 0700, arquivos 0600), fora de data/sites (a raiz pública) e de data/catalogs.
Por isso remover um site ou uma loja NÃO apaga os backups dela.

O que entra: arquivos do site + catálogo (products.json e fotos). O que nunca entra: senhas/hash de senha
(store.json), .env, chaves, tokens, cookies, sessões, arquivos ocultos e links simbólicos.

Regras que o código garante (e os testes conferem):
  • "Concluído" só depois de reabrir o ZIP e conferir inventário, tamanhos e hashes;
  • restaurar valida de novo, protege o estado atual com outro backup, prepara tudo numa pasta isolada,
    troca, confere o resultado e, se algo falhar, volta ao que estava (inclusive depois de o Termux ser morto);
  • nada é apagado para liberar espaço; a exclusão é manual, com confirmação, e a política automática fica DESLIGADA;
  • uma loja nunca enxerga o backup de outra;
  • a senha de recuperação nunca é gravada, registrada ou devolvida ao navegador.
Biblioteca padrão + `cryptography` (só para criptografar; sem ela tudo funciona, menos criar backups novos).
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import secrets
import shutil
import stat
import threading
import time
import zipfile
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO, Callable

import backup_crypto as bcrypto
import catalog

FORMAT = "cdbs-backup"
FORMAT_VERSION = 1
MAX_BACKUP_FILES = 5000                 # limite de segurança por backup (não é cota da loja)
MAX_BACKUP_BYTES = 1024 * 1024 * 1024   # idem: 1 GB descompactado
MAX_MANIFEST_BYTES = 8 * 1024 * 1024
SPACE_MARGIN = 20 * 1024 * 1024
REMINDER_DAYS = 30
REMINDER_TICK_SECONDS = 6 * 3600        # verificação periódica enquanto o CDBS estiver rodando
PASSWORD_FAILS, PASSWORD_WINDOW, PASSWORD_LOCK = 5, 300, 60   # 5 erros em 5 min ⇒ espera de 1 min
MIN_KEEP_LAST, MIN_KEEP_DAYS = 1, 7
REMINDER_LOG_SECONDS = 24 * 3600        # no máximo um registro de aviso por loja por dia
CONFIRM_TTL = 600
HISTORY_LIMIT = 100
SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,30}[a-z0-9])?$")
ID_RE = re.compile(r"^[0-9a-f]{12}$")
SECRET_NAME_RE = re.compile(r"(?i)^(\.env.*|.*\.(pem|key|p12|pfx)|id_rsa.*|id_ed25519.*|secret\.key|admin\.json|providers\.json|store\.json)$")

# Estados
CRIANDO, VALIDANDO, CONCLUIDO, FALHOU, INVALIDO = "CRIANDO", "VALIDANDO", "CONCLUIDO", "FALHOU", "INVALIDO"
PENDENTE, PREPARANDO, APLICANDO, VERIFICANDO, REVERTIDO = "PENDENTE", "PREPARANDO", "APLICANDO", "VERIFICANDO", "REVERTIDO"
EXCLUIDO = "EXCLUIDO"
ACTIVE = frozenset({CRIANDO, VALIDANDO, PENDENTE, PREPARANDO, APLICANDO, VERIFICANDO})
BACKUP_TRANSITIONS = {
    CRIANDO: {VALIDANDO, FALHOU}, VALIDANDO: {CONCLUIDO, INVALIDO, FALHOU},
    CONCLUIDO: {INVALIDO, EXCLUIDO}, INVALIDO: {CONCLUIDO, EXCLUIDO}, FALHOU: set(), EXCLUIDO: set(),
}
RESTORE_TRANSITIONS = {
    PENDENTE: {VALIDANDO, FALHOU}, VALIDANDO: {PREPARANDO, FALHOU}, PREPARANDO: {APLICANDO, FALHOU},
    APLICANDO: {VERIFICANDO, REVERTIDO, FALHOU}, VERIFICANDO: {CONCLUIDO, REVERTIDO},
    CONCLUIDO: set(), FALHOU: set(), REVERTIDO: set(),
}
KINDS_BACKUP = ("manual", "seguranca", "importado", "migrado")

POLICY = (
    "Os backups ficam só neste celular, numa pasta privada fora dos sites. Eles NÃO entram em nenhuma cota de loja: "
    "o CDBS não impõe cota de espaço (o limite de 500 MB por loja é só uma proposta e não está ativo). "
    f"Cada backup tem um limite de segurança de {MAX_BACKUP_BYTES // (1024 * 1024)} MB e {MAX_BACKUP_FILES} arquivos. "
    "Nada é apagado automaticamente: se o espaço acabar, você decide o que excluir (botão Excluir, com confirmação). "
    "Backup guardado só no celular não protege contra perda, dano ou formatação do aparelho: exporte uma cópia."
)

ERRORS: dict[str, tuple[str, str, int]] = {
    "SEM_DADOS": ("Esta loja não tem site nem catálogo para guardar.", "Publique um site ou ative o catálogo primeiro.", 409),
    "ESPACO_INSUFICIENTE": ("Não há espaço livre suficiente neste celular.", "Libere espaço (apague arquivos que você não usa) e tente de novo. Nada foi alterado.", 507),
    "LIMITE_TAMANHO": ("A loja passa do limite de segurança de um backup.", "Reduza o conteúdo ou fale com o suporte.", 413),
    "EM_ANDAMENTO": ("Já existe uma operação de backup ou restauração nesta loja.", "Espere terminar. A tela mostra o andamento.", 409),
    "NAO_ENCONTRADO": ("Não encontrado.", "", 404),
    "PEDIDO_INVALIDO": ("Pedido inválido.", "", 400),
    "BACKUP_INVALIDO": ("O backup está inválido e não pode ser usado.", "Crie um backup novo. Este não foi tocado.", 422),
    "CONFIRMACAO": ("A confirmação é inválida ou expirou.", "Abra o resumo da restauração de novo e confirme.", 403),
    "ESTADO_INVALIDO": ("Esta ação não vale para o estado atual.", "", 409),
    "FALHA_BACKUP_SEGURANCA": ("Não foi possível proteger o estado atual antes de restaurar.", "Nada foi alterado. Veja o motivo e tente de novo.", 500),
    "VERIFICACAO_FALHOU": ("O resultado da restauração não conferiu com o backup.", "A versão anterior foi recolocada. Nada foi perdido.", 500),
    "INTERROMPIDO": ("A operação foi interrompida (o aplicativo foi reiniciado).", "Confira o histórico; se era uma restauração, a versão anterior foi recolocada.", 409),
    "FALHA_CRIACAO": ("O backup não pôde ser criado.", "Tente de novo. Nada do site foi alterado.", 500),
    "ARQUIVO_MUITO_GRANDE": ("O arquivo enviado passa do limite.", "", 413),
    "ERRO_DESCONHECIDO": ("Erro inesperado.", "Tente de novo. Se repetir, confira o espaço livre.", 500),
    "AINDA_NAO_VALIDADO": ("Só backups validados podem ser exportados ou restaurados.", "Toque em Verificar integridade.", 409),
    "PROTECAO_INDISPONIVEL": ("A criptografia não está disponível neste aparelho, então nenhum backup novo foi criado.",
                              "No Termux instale o pacote com: pkg install python-cryptography — e reinicie o CDBS. Backups antigos continuam podendo ser listados e baixados.", 503),
    "PROTECAO_NAO_CONFIGURADA": ("Falta definir a senha de recuperação dos backups.", "Defina a senha em “Proteção dos backups” na Central e tente de novo.", 409),
    "PROTECAO_JA_CONFIGURADA": ("A senha de recuperação já foi definida.", "Use a senha atual. Este CDBS não troca nem recupera essa senha.", 409),
    "SENHA_INCORRETA": ("Senha de recuperação incorreta.", "Digite a senha definida na Central. Se a esqueceu, não há como recuperá-la (veja docs/CRIPTOGRAFIA.md).", 403),
    "SENHA_INVALIDA": ("A senha não atende aos requisitos.", "Use pelo menos 12 caracteres, sem repetir o mesmo símbolo, e confirme digitando de novo.", 400),
    "SENHA_BLOQUEADA": ("Muitas tentativas de senha seguidas.", "Espere um minuto e tente de novo.", 429),
    "OUTRA_SENHA": ("Este arquivo foi protegido com outra senha.", "Para importar, use a mesma senha de recuperação com que ele foi criado: configure-a neste CDBS primeiro.", 422),
    "BACKUP_EM_USO": ("Este backup está em uso por uma operação em andamento.", "Espere a operação terminar.", 409),
    "ULTIMO_VALIDADO": ("Este é o último backup validado desta loja.", "Para excluí-lo, marque também que entende o risco.", 409),
    "EXCLUSAO_INVALIDA": ("Só dá para excluir um backup concluído ou inválido que ainda tenha arquivo.", "", 409),
    "POLITICA_INVALIDA": ("A política de retenção é inválida.", "Escolha “manter tudo”, uma quantidade (mínimo 1) ou um período (mínimo 7 dias).", 400),
    "SITUACAO_MUDOU": ("A situação da loja mudou desde o resumo.", "Abra o resumo da restauração de novo e confirme.", 409),
    "LOJA_JA_EXISTE": ("Já existe uma loja com este nome; ela não será substituída.", "Para trocar o conteúdo dela, use a restauração normal.", 409),
    "JA_PROTEGIDO": ("Este backup já está protegido.", "", 409),
}
VALIDATION_MESSAGES = {
    "ARQUIVO_NAO_ENCONTRADO": "O arquivo do backup não existe mais neste celular.",
    "ARQUIVO_CORROMPIDO": "O arquivo do backup está corrompido ou incompleto (truncado).",
    "ARQUIVO_ALTERADO": "O arquivo do backup foi alterado depois de criado.",
    "ARQUIVO_AUSENTE": "Falta no arquivo um item que o inventário lista.",
    "INVENTARIO_DIVERGENTE": "O arquivo tem itens que não constam no inventário.",
    "HASH_DIVERGENTE": "O conteúdo de um item não confere com o hash registrado.",
    "VERSAO_INCOMPATIVEL": "O backup foi feito por uma versão incompatível do formato.",
    "OUTRA_LOJA": "Este backup pertence a outra loja.",
    "CONTEUDO_PROIBIDO": "O backup contém item não permitido (segredo ou tipo inesperado).",
    "CAMINHO_INSEGURO": "O backup contém um caminho inseguro.",
    "LIMITE_EXCEDIDO": "O backup passa dos limites de tamanho ou quantidade.",
    "MANIFESTO_INVALIDO": "O inventário do backup está ausente ou ilegível.",
    "SENHA_INCORRETA": "Não foi possível abrir o backup protegido com esta senha.",
    "FORMATO_PROTEGIDO_INVALIDO": "O arquivo protegido não está no formato esperado.",
}


class BackupError(Exception):
    def __init__(self, code: str, message: str | None = None, *, status: int | None = None, detail: str = "", record: dict | None = None):
        base, _, default_status = ERRORS.get(code, ERRORS["ERRO_DESCONHECIDO"])
        super().__init__(message or base)
        self.code, self.status, self.detail, self.record = code, status or default_status, detail, record

    @property
    def next_action(self) -> str:
        return ERRORS.get(self.code, ERRORS["ERRO_DESCONHECIDO"])[1]


# --------------------------------------------------------------------------- utilidades

def _chmod(path: Path, mode: int) -> None:
    try:
        os.chmod(path, mode)
    except OSError:
        pass


def _ensure_dir(path: Path, mode: int = 0o700) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _chmod(path, mode)


def _write_json_private(path: Path, data: object) -> None:
    _ensure_dir(path.parent)
    temp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _sha256_file(path: Path) -> tuple[str, int]:
    digest, size = hashlib.sha256(), 0
    with path.open("rb") as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def dir_size(path: Path) -> int:
    total = 0
    try:
        for current, dirnames, filenames in os.walk(path, followlinks=False):
            for name in filenames:
                try:
                    info = os.lstat(os.path.join(current, name))
                except OSError:
                    continue
                if stat.S_ISREG(info.st_mode):
                    total += info.st_size
    except OSError:
        return 0
    return total


def _safe_arc(name: str) -> bool:
    if not name or len(name) > 240 or "\\" in name or name.startswith("/") or name.endswith("/"):
        return False
    parts = name.split("/")
    if ":" in parts[0]:
        return False
    return not any(p in ("", ".", "..") or p.startswith(".") or any(ord(c) < 32 for c in p) for p in parts)


def _allowed_arc(arc: str) -> str | None:
    """Devolve a 'parte' (site|catalog) se o caminho dentro do ZIP é permitido; senão None."""
    if not _safe_arc(arc):
        return None
    parts = arc.split("/")
    if parts[0] == "site" and len(parts) >= 2:
        return None if SECRET_NAME_RE.match(parts[-1]) else "site"
    if parts[0] == "catalog":
        if arc in ("catalog/products.json", "catalog/store_info.json"):
            return "catalog"
        if len(parts) == 3 and parts[1] == "media" and catalog.IMAGE_NAME_RE.fullmatch(parts[2]):
            return "catalog"
    return None


# --------------------------------------------------------------------------- validação do ZIP

@dataclass
class Validation:
    ok: bool
    code: str = ""
    detail: str = ""
    files: int = 0
    bytes: int = 0
    manifest: dict = field(default_factory=dict)
    mode: str = "completa"          # completa (ZIP legado) | protegida (aberto com a senha e conferido) | arquivo (só o arquivo, sem senha)

    def view(self, now: int) -> dict:
        good = {"completa": "Íntegro: todos os arquivos conferem com o inventário.",
                "protegida": "Íntegro e autenticado: o arquivo abre com a senha e todos os itens conferem com o inventário.",
                "arquivo": "Arquivo íntegro (tamanho e SHA-256 conferem). O conteúdo protegido só é conferido com a senha de recuperação."}[self.mode]
        message = good if self.ok else VALIDATION_MESSAGES.get(self.code, "Backup inválido.")
        return {"ok": self.ok, "code": self.code, "message": message + (f" ({self.detail})" if self.detail and not self.ok else ""),
                "files": self.files, "bytes": self.bytes, "checked_at": now, "mode": self.mode}


def validate_archive(path: Path, expected_slug: str, *, expected_size: int | None = None, expected_sha: str | None = None,
                     max_files: int = MAX_BACKUP_FILES, max_bytes: int = MAX_BACKUP_BYTES) -> Validation:
    """Confere o ZIP de ponta a ponta. Não confia em cabeçalhos: mede o que lê."""
    def bad(code: str, detail: str = "") -> Validation:
        return Validation(False, code, detail)

    try:
        info = os.lstat(path)
    except OSError:
        return bad("ARQUIVO_NAO_ENCONTRADO")
    if not stat.S_ISREG(info.st_mode) or info.st_size == 0:
        return bad("ARQUIVO_CORROMPIDO")
    if expected_size is not None and info.st_size != expected_size:
        return bad("ARQUIVO_ALTERADO", "tamanho diferente do registrado")
    try:
        if expected_sha is not None and _sha256_file(path)[0] != expected_sha:
            return bad("ARQUIVO_ALTERADO", "impressão digital diferente da registrada")
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            names = [item.filename for item in infos]
            if len(names) > max_files + 1:
                return bad("LIMITE_EXCEDIDO", "itens demais")
            if len(set(names)) != len(names):
                return bad("CAMINHO_INSEGURO", "itens duplicados")
            if names.count("manifest.json") != 1:
                return bad("MANIFESTO_INVALIDO")
            for name in names:
                if name != "manifest.json" and _allowed_arc(name) is None:
                    return bad("CONTEUDO_PROIBIDO" if _safe_arc(name) else "CAMINHO_INSEGURO", "item não permitido" if _safe_arc(name) else "caminho inválido")
            manifest_info = archive.getinfo("manifest.json")
            if manifest_info.file_size > MAX_MANIFEST_BYTES:
                return bad("MANIFESTO_INVALIDO")
            with archive.open(manifest_info) as handle:
                raw = handle.read(MAX_MANIFEST_BYTES + 1)
            if len(raw) > MAX_MANIFEST_BYTES:
                return bad("MANIFESTO_INVALIDO")
            try:
                manifest = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                return bad("MANIFESTO_INVALIDO")
            if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
                return bad("MANIFESTO_INVALIDO")
            if manifest.get("version") != FORMAT_VERSION:
                return bad("VERSAO_INCOMPATIVEL", f"versão {str(manifest.get('version'))[:12]}")
            if manifest.get("slug") != expected_slug:
                return bad("OUTRA_LOJA")
            listed = manifest.get("files")
            if not isinstance(listed, list) or len(listed) > max_files:
                return bad("MANIFESTO_INVALIDO")
            declared: dict[str, dict] = {}
            total = 0
            for item in listed:
                if (not isinstance(item, dict) or not isinstance(item.get("path"), str) or not isinstance(item.get("size"), int)
                        or isinstance(item.get("size"), bool) or item["size"] < 0
                        or not isinstance(item.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"])):
                    return bad("MANIFESTO_INVALIDO")
                if _allowed_arc(item["path"]) is None:
                    return bad("CONTEUDO_PROIBIDO" if _safe_arc(item["path"]) else "CAMINHO_INSEGURO", "item não permitido")
                if item["path"] in declared:
                    return bad("MANIFESTO_INVALIDO", "item repetido")
                declared[item["path"]] = item
                total += item["size"]
            if total > max_bytes:
                return bad("LIMITE_EXCEDIDO", "tamanho total")
            present = {n for n in names if n != "manifest.json"}
            missing = sorted(set(declared) - present)
            if missing:
                return bad("ARQUIVO_AUSENTE", missing[0])
            extra = sorted(present - set(declared))
            if extra:
                return bad("INVENTARIO_DIVERGENTE", extra[0])
            read_total = 0
            for item in infos:
                if item.filename == "manifest.json":
                    continue
                want = declared[item.filename]
                digest, size = hashlib.sha256(), 0
                with archive.open(item) as handle:
                    while chunk := handle.read(1 << 20):
                        size += len(chunk)
                        if size > want["size"]:
                            return bad("HASH_DIVERGENTE", item.filename)   # maior que o declarado (também barra ZIP bomba)
                        digest.update(chunk)
                if size != want["size"] or digest.hexdigest() != want["sha256"]:
                    return bad("HASH_DIVERGENTE", item.filename)
                read_total += size
            return Validation(True, files=len(declared), bytes=read_total, manifest=manifest)
    except zipfile.BadZipFile:
        return bad("ARQUIVO_CORROMPIDO", "estrutura do ZIP inválida")
    except (OSError, EOFError, zlib.error, NotImplementedError, RuntimeError):
        return bad("ARQUIVO_CORROMPIDO", "falha ao ler o conteúdo")


def verify_tree(root: Path, expected: list[dict], *, prefix: str) -> tuple[bool, str]:
    """Compara uma pasta com os itens do inventário (mesma lista, tamanhos e SHA-256)."""
    want = {item["path"][len(prefix):]: item for item in expected if item["path"].startswith(prefix)}
    found: dict[str, Path] = {}
    if root.is_dir():
        for current, _dirs, filenames in os.walk(root, followlinks=False):
            for name in filenames:
                full = Path(current) / name
                if full.is_symlink():
                    return False, "link inesperado"
                found[full.relative_to(root).as_posix()] = full
    if set(found) != set(want):
        return False, "lista de arquivos diferente"
    for rel, item in want.items():
        digest, size = _sha256_file(found[rel])
        if size != item["size"] or digest != item["sha256"]:
            return False, "conteúdo diferente"
    return True, ""


class RateGate:
    """Limita quantos pedidos que alteram dados entram por janela de tempo."""

    def __init__(self, limit: int, window: float, clock: Callable[[], float] = time.time):
        self.limit, self.window, self._clock = limit, window, clock
        self._hits: list[float] = []
        self._lock = threading.Lock()

    def allow(self) -> bool:
        with self._lock:
            now = self._clock()
            self._hits = [t for t in self._hits if now - t < self.window]
            if len(self._hits) >= self.limit:
                return False
            self._hits.append(now)
            return True


# --------------------------------------------------------------------------- serviço

@dataclass
class Entry:
    arc: str
    path: Path
    size: int
    part: str


class BackupService:
    def __init__(self, data_dir: Path, sites_dir: Path, catalog_dir: Path, *, clock: Callable[[], float] = time.time,
                 disk_free: Callable[[], int] | None = None, max_files: int = MAX_BACKUP_FILES, max_bytes: int = MAX_BACKUP_BYTES,
                 kdf: bcrypto.KdfParams | None = None):
        self.data = Path(data_dir)
        self.sites = Path(sites_dir)
        self.catalogs = Path(catalog_dir)
        self._clock = clock
        self._disk_free = disk_free or (lambda: shutil.disk_usage(self.data).free)
        self.max_files, self.max_bytes = max_files, max_bytes
        self.hooks: dict[str, Callable[..., None]] = {}      # só os testes usam: injetam falhas em pontos exatos
        self._rec_lock = threading.RLock()
        self._guard = threading.Lock()
        self._active: dict[str, str] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._confirm: dict[str, dict] = {}
        self.kdf = (kdf or bcrypto.KdfParams()).check()
        self._pw_fails: list[float] = []
        self._pw_lock_until = 0.0
        self._maint: dict[str, int] = {}                     # lojas em troca de arquivos neste instante (visitante recebe 503, não 404)
        self.observer: Callable[[dict], None] | None = None  # avisado quando uma operação termina (histórico de auditoria)

    # ----- caminhos
    @property
    def root(self) -> Path:
        return self.data / "backups"

    @property
    def tmp_root(self) -> Path:
        return self.data / "restore-tmp"

    def _now(self) -> int:
        return int(self._clock())

    def _hook(self, name: str, *args) -> None:
        hook = self.hooks.get(name)
        if hook:
            hook(*args)

    @staticmethod
    def _slug(slug: object) -> str:
        if not isinstance(slug, str) or not SLUG_RE.fullmatch(slug):
            raise BackupError("NAO_ENCONTRADO")
        return slug

    @staticmethod
    def _rid(rid: object) -> str:
        if not isinstance(rid, str) or not ID_RE.fullmatch(rid):
            raise BackupError("NAO_ENCONTRADO")
        return rid

    def _slug_dir(self, slug: str) -> Path:
        return self.root / slug

    def known(self, slug: str) -> bool:
        slug = self._slug(slug)
        return ((self.sites / slug / "index.html").is_file() or (self.catalogs / slug / "store.json").is_file()
                or self._slug_dir(slug).is_dir())

    def _require_known(self, slug: str) -> str:
        slug = self._slug(slug)
        if not self.known(slug):
            raise BackupError("NAO_ENCONTRADO", "Loja ou site não encontrado.")
        return slug

    def _rec_path(self, slug: str, rid: str) -> Path:
        return self._slug_dir(slug) / f"{rid}.json"

    def _zip_path(self, slug: str, rid: str) -> Path:
        return self._slug_dir(slug) / f"{rid}.zip"

    def _enc_path(self, slug: str, rid: str) -> Path:
        return self._slug_dir(slug) / f"{rid}{bcrypto.FILE_EXTENSION}"

    @staticmethod
    def _is_encrypted(rec: dict) -> bool:
        return (rec.get("protection") or {}).get("state") == "CRIPTOGRAFADO"

    def _file_path(self, rec: dict) -> Path:
        """Onde está o arquivo do backup: .cdbk (protegido) ou .zip (legado, da Evolução 2)."""
        return self._enc_path(rec["slug"], rec["id"]) if self._is_encrypted(rec) else self._zip_path(rec["slug"], rec["id"])

    # ----- manutenção: avisa o servidor público durante a troca de arquivos
    def in_maintenance(self, slug: str) -> bool:
        with self._guard:
            return self._maint.get(slug, 0) > 0

    def _maintenance(self, slug: str):
        service = self

        class _Gate:
            def __enter__(self_inner):
                with service._guard:
                    service._maint[slug] = service._maint.get(slug, 0) + 1

            def __exit__(self_inner, *exc):
                with service._guard:
                    left = service._maint.get(slug, 1) - 1
                    if left > 0:
                        service._maint[slug] = left
                    else:
                        service._maint.pop(slug, None)
                return False
        return _Gate()

    # ----- senha de recuperação (nunca gravada; só um verificador contra erro de digitação)
    def _protection_path(self) -> Path:
        return self.root / "protection.json"

    def protection_status(self) -> dict:
        config = _read_json(self._protection_path())
        configured = bool(config.get("check"))
        try:
            kdf = bcrypto.KdfParams(**{k: int(v) for k, v in config["params"].items()}).describe() if configured else self.kdf.describe()
        except (KeyError, TypeError, ValueError):
            kdf = self.kdf.describe()
        return {"available": bcrypto.available(), "reason": bcrypto.unavailable_reason(), "configured": configured, "format": "CDBK",
                "version": bcrypto.VERSION, "cipher": bcrypto.CIPHER_NAME, "kdf": kdf, "created_at": config.get("created_at"),
                "min_chars": bcrypto.MIN_PASSWORD_CHARS,
                "warning": "A senha de recuperação não é guardada em lugar nenhum e não pode ser recuperada. Sem ela, os backups protegidos não abrem. "
                           "Guarde-a num gerenciador de senhas ou anotada em local seguro, fora do celular."}

    def setup_protection(self, password: object, confirm: object) -> dict:
        if not bcrypto.available():
            raise BackupError("PROTECAO_INDISPONIVEL")
        if self.protection_status()["configured"]:
            raise BackupError("PROTECAO_JA_CONFIGURADA")
        if not isinstance(password, str) or password != confirm:
            raise BackupError("SENHA_INVALIDA", "A confirmação não é igual à senha.")
        try:
            bcrypto.check_password_strength(password)
        except bcrypto.CryptoError:
            raise BackupError("SENHA_INVALIDA") from None
        with self._rec_lock:
            if self.protection_status()["configured"]:
                raise BackupError("PROTECAO_JA_CONFIGURADA")
            verifier = bcrypto.make_verifier(password, self.kdf)
            verifier["created_at"] = self._now()
            _write_json_private(self._protection_path(), verifier)
        self._event("-", "protecao_configurada", "Senha de recuperação definida (o valor não é guardado).")
        return self.protection_status()

    def _check_password(self, password: object) -> None:
        """Confere a senha digitada contra o verificador. Falha sem revelar nada além de 'incorreta' e trava após erros em série."""
        if not bcrypto.available():
            raise BackupError("PROTECAO_INDISPONIVEL")
        config = _read_json(self._protection_path())
        if not config.get("check"):
            raise BackupError("PROTECAO_NAO_CONFIGURADA")
        now = self._clock()
        with self._guard:
            if now < self._pw_lock_until:
                raise BackupError("SENHA_BLOQUEADA")
        ok = isinstance(password, str) and bool(password) and bcrypto.check_verifier(config, password)
        if ok:
            with self._guard:
                self._pw_fails.clear()
            return
        with self._guard:
            self._pw_fails = [t for t in self._pw_fails if now - t < PASSWORD_WINDOW] + [now]
            locked = len(self._pw_fails) >= PASSWORD_FAILS
            if locked:
                self._pw_lock_until = now + PASSWORD_LOCK
                self._pw_fails.clear()
        self._event("-", "senha_incorreta", "Tentativa com senha de recuperação incorreta." + (" Bloqueio de 1 minuto." if locked else ""))
        raise BackupError("SENHA_INCORRETA")

    # ----- registros
    def _load(self, slug: str, rid: str) -> dict:
        data = _read_json(self._rec_path(self._slug(slug), self._rid(rid)))
        if not data or data.get("slug") != slug or data.get("id") != rid:     # nunca confia só no id recebido
            raise BackupError("NAO_ENCONTRADO")
        return data

    def _save(self, rec: dict) -> None:
        _write_json_private(self._rec_path(rec["slug"], rec["id"]), rec)

    def _records(self, slug: str) -> list[dict]:
        folder = self._slug_dir(slug)
        out = []
        if folder.is_dir():
            for path in folder.glob("*.json"):
                if ID_RE.fullmatch(path.stem):
                    data = _read_json(path)
                    if data.get("slug") == slug and data.get("id") == path.stem:
                        out.append(data)
        return sorted(out, key=lambda r: (r.get("started_at", 0), r["id"]), reverse=True)

    def _new_record(self, slug: str, kind: str, **fields) -> dict:
        now = self._now()
        first = "PENDENTE" if kind == "restauracao" else CRIANDO
        rec = {"id": secrets.token_hex(6), "slug": slug, "kind": kind, "state": first, "started_at": now, "finished_at": None,
               "duration_s": None, "steps": [{"state": first, "at": now}], "error_code": "", "error_message": "", "next_action": "",
               "validation": None, "size_bytes": None, "zip_sha256": None, "files": 0, "source_bytes": 0, "parts": [],
               "warnings": [], "skipped": 0, "progress": {"done": 0, "total": 0}, "exports": [], "origin": "painel-admin",
               "source_backup": None, "safety_backup": None, "restored": None, "imported_from": None, "format_version": FORMAT_VERSION,
               "protection": None, "deleted_at": None}
        rec.update(fields)
        with self._rec_lock:
            _ensure_dir(self.root)                       # a pasta raiz também é privada (0700), não só a de cada loja
            _ensure_dir(self._slug_dir(slug))
            self._save(rec)
        return rec

    def _set(self, slug: str, rid: str, state: str, **fields) -> dict:
        with self._rec_lock:
            rec = self._load(slug, rid)
            table = RESTORE_TRANSITIONS if rec["kind"] == "restauracao" else BACKUP_TRANSITIONS
            if state != rec["state"] and state not in table.get(rec["state"], set()):
                raise ValueError(f"transição inválida {rec['state']} -> {state}")
            now = self._now()
            changed = state != rec["state"]
            rec.update(fields)
            if changed:
                rec["steps"] = (rec["steps"] + [{"state": state, "at": now}])[-30:]
            rec["state"] = state
            if state in (CONCLUIDO, FALHOU, REVERTIDO) and rec["finished_at"] is None:
                rec["finished_at"], rec["duration_s"] = now, max(0, now - rec["started_at"])
            self._save(rec)
            snapshot = dict(rec)
        if changed and state in (CONCLUIDO, FALHOU, INVALIDO, REVERTIDO, EXCLUIDO) and self.observer is not None:
            try:
                self.observer(snapshot)                       # fora da trava: o observador nunca bloqueia nem derruba a Central
            except Exception:                                 # noqa: BLE001
                pass
        return rec

    def _patch(self, slug: str, rid: str, **fields) -> dict:
        with self._rec_lock:
            rec = self._load(slug, rid)
            rec.update(fields)
            self._save(rec)
            return rec

    def _event(self, slug: str, kind: str, message: str) -> None:
        """Eventos soltos (aviso mensal, falha ao consultar espaço, exportação). Sem dados sensíveis."""
        with self._rec_lock:
            path = self.root / "events.json"
            data = _read_json(path)
            items = data.get("events", [])
            items.append({"at": self._now(), "slug": slug, "kind": kind, "message": message[:200]})
            _write_json_private(path, {"events": items[-300:]})

    def events(self, slug: str | None = None, limit: int = 50) -> list[dict]:
        items = _read_json(self.root / "events.json").get("events", [])
        return [e for e in reversed(items) if slug is None or e.get("slug") == slug][:limit]

    # ----- visão pública de um registro
    def view(self, rec: dict) -> dict:
        active = rec["state"] in ACTIVE
        validated = rec["state"] == CONCLUIDO and rec["kind"] != "restauracao"
        prot = rec.get("protection") or {}
        encrypted = prot.get("state") == "CRIPTOGRAFADO"
        is_backup = rec["kind"] in KINDS_BACKUP
        protection = None
        if is_backup and (encrypted or rec["state"] in (CONCLUIDO, INVALIDO)):          # um registro que falhou não tem arquivo: não rotular como LEGADO
            protection = ({"state": "CRIPTOGRAFADO", "format": prot.get("format", "CDBK"), "version": prot.get("version", bcrypto.VERSION),
                           "cipher": prot.get("cipher", bcrypto.CIPHER_NAME), "kdf": prot.get("kdf", ""), "needs_password": True,
                           "content_verified_at": prot.get("content_verified_at"),
                           "label": "Criptografado (AES-256-GCM). Precisa da senha de recuperação para abrir."}
                          if encrypted else
                          {"state": "LEGADO", "format": "ZIP", "version": rec.get("format_version", 1), "cipher": None, "kdf": None, "needs_password": False,
                           "content_verified_at": None,
                           "label": "LEGADO: arquivo ZIP SEM criptografia (feito pela Evolução 2). Quem tiver o arquivo consegue ler. Use “Proteger” para criar uma cópia criptografada."})
        return {
            "id": rec["id"], "short_id": rec["id"][:8], "slug": rec["slug"], "kind": rec["kind"], "state": rec["state"],
            "started_at": rec["started_at"], "finished_at": rec["finished_at"],
            "duration_s": rec["duration_s"] if rec["duration_s"] is not None else (max(0, self._now() - rec["started_at"]) if active else None),
            "error_code": rec["error_code"], "error_message": rec["error_message"], "next_action": rec["next_action"],
            "validation": rec["validation"], "size_bytes": rec["size_bytes"], "files": rec["files"], "source_bytes": rec["source_bytes"],
            "parts": rec["parts"], "warnings": rec["warnings"][:10], "skipped": rec["skipped"], "progress": rec["progress"],
            "exports": rec["exports"][-5:], "origin": rec["origin"], "source_backup": rec["source_backup"],
            "safety_backup": rec["safety_backup"], "restored": rec["restored"], "imported_from": rec["imported_from"],
            "zip_sha256": (rec["zip_sha256"] or "")[:16], "steps": rec["steps"], "protection": protection,
            "deleted_at": rec.get("deleted_at"),
            "can_export": validated, "can_restore": validated, "can_validate": is_backup and not active and rec["state"] in (CONCLUIDO, INVALIDO),
            "can_protect": validated and is_backup and not encrypted,
            "can_delete": is_backup and not active and rec["state"] in (CONCLUIDO, INVALIDO),
        }

    # ----- espaço
    def _free(self, slug: str) -> int | None:
        try:
            return int(self._disk_free())
        except Exception:                                                    # noqa: BLE001
            self._event(slug, "falha_espaco", "Não foi possível consultar o espaço livre do aparelho.")
            return None

    def _need_space(self, slug: str, needed: int) -> list[str]:
        free = self._free(slug)
        if free is None:
            return ["Não foi possível consultar o espaço livre; a operação seguiu sem essa conferência."]
        if free < needed + SPACE_MARGIN:
            raise BackupError("ESPACO_INSUFICIENTE", f"Faltam {(needed + SPACE_MARGIN - free) // (1024 * 1024) + 1} MB livres para esta operação.")
        return []

    # ----- coleta dos arquivos
    def _gather_site(self, slug: str, skipped: list[str]) -> list[Entry]:
        root = self.sites / slug
        entries: list[Entry] = []
        if root.is_symlink() or not root.is_dir():
            return entries
        for current, dirnames, filenames in os.walk(root, followlinks=False):
            keep = []
            for name in sorted(dirnames):
                full = Path(current) / name
                if name.startswith(".") or full.is_symlink():
                    skipped.append(f"{name}/")
                else:
                    keep.append(name)
            dirnames[:] = keep
            for name in sorted(filenames):
                full = Path(current) / name
                try:
                    info = os.lstat(full)
                except OSError:
                    skipped.append(name)
                    continue
                if name.startswith(".") or SECRET_NAME_RE.match(name) or not stat.S_ISREG(info.st_mode):
                    skipped.append(name)
                    continue
                rel = full.relative_to(root).as_posix()
                if _allowed_arc("site/" + rel) is None:
                    skipped.append(name)
                    continue
                entries.append(Entry("site/" + rel, full, info.st_size, "site"))
        return entries

    def _gather_catalog(self, slug: str, skipped: list[str]) -> tuple[list[Entry], dict | None]:
        base = self.catalogs / slug
        store = _read_json(base / "store.json")
        if not store:
            return [], None
        entries = []
        products = base / "products.json"
        if products.is_file() and not products.is_symlink():
            entries.append(Entry("catalog/products.json", products, products.stat().st_size, "catalog"))
        media = base / "media"
        if media.is_dir() and not media.is_symlink():
            for item in sorted(media.iterdir(), key=lambda p: p.name):
                if item.is_file() and not item.is_symlink() and catalog.IMAGE_NAME_RE.fullmatch(item.name):
                    entries.append(Entry(f"catalog/media/{item.name}", item, item.stat().st_size, "catalog"))
                else:
                    skipped.append(item.name)
        info = {"name": str(store.get("name", ""))[:60], "whatsapp": str(store.get("whatsapp", ""))[:20], "created": store.get("created")}
        return entries, info

    # ----- criar
    def start_backup(self, slug: str, password: object = None, *, plaintext: bool = False) -> tuple[dict, bool]:
        """Backup manual. Sempre protegido por senha. `plaintext=True` só existe para testes e para o backup de segurança
        antes de restaurar quando a biblioteca de criptografia está ausente; nenhuma rota o aceita."""
        slug = self._require_known(slug)
        if not plaintext:
            self._check_password(password)                  # recusa antes de criar qualquer registro
        with self._guard:
            active = self._active.get(slug)
            if active:
                return self.view(self._load(slug, active)), False
            rec = self._new_record(slug, "manual")
            self._active[slug] = rec["id"]
        self._touch_seen(slug)
        secret = None if plaintext else password
        self._spawn(rec["id"], lambda: self._run_backup(slug, rec["id"], secret))
        return self.view(rec), True

    def _spawn(self, rid: str, target: Callable[[], None]) -> None:
        thread = threading.Thread(target=target, name=f"backup-{rid}", daemon=True)
        self._threads[rid] = thread
        thread.start()

    def wait(self, rid: str, timeout: float = 30) -> None:
        thread = self._threads.get(rid)
        if thread:
            thread.join(timeout)

    def _release(self, slug: str, rid: str) -> None:
        with self._guard:
            if self._active.get(slug) == rid:
                del self._active[slug]

    def _run_backup(self, slug: str, rid: str, password: object) -> None:
        try:
            self._do_backup(slug, rid, password)
        except BackupError as err:
            self._fail(slug, rid, err)
        except bcrypto.CryptoError:
            self._fail(slug, rid, BackupError("FALHA_CRIACAO", "Não foi possível proteger o backup com a senha."))
        except Exception:                                                    # noqa: BLE001 — nada interno vaza
            self._fail(slug, rid, BackupError("FALHA_CRIACAO"))
        finally:
            self._cleanup_tmp(rid)
            self._release(slug, rid)

    def _fail(self, slug: str, rid: str, err: BackupError) -> None:
        for leftover in self._slug_dir(slug).glob(f".{rid}.part"):
            leftover.unlink(missing_ok=True)
        try:
            rec = self._load(slug, rid)
            if rec["state"] in (FALHOU, CONCLUIDO, INVALIDO, EXCLUIDO):
                return
            self._enc_path(slug, rid).unlink(missing_ok=True)                # um registro FALHOU nunca deixa arquivo órfão
            self._zip_path(slug, rid).unlink(missing_ok=True)
            self._set(slug, rid, FALHOU, error_code=err.code, error_message=str(err), next_action=err.next_action)
        except (BackupError, ValueError):
            pass

    def _do_backup(self, slug: str, rid: str, password: object) -> None:
        skipped: list[str] = []
        site_entries = self._gather_site(slug, skipped)
        catalog_entries, store_info = self._gather_catalog(slug, skipped)
        entries = site_entries + catalog_entries
        if not entries:
            raise BackupError("SEM_DADOS")
        total = sum(e.size for e in entries)
        if len(entries) > self.max_files or total > self.max_bytes:
            raise BackupError("LIMITE_TAMANHO")
        encrypted = password is not None
        warnings = self._need_space(slug, total * 2 if encrypted else total)      # protegido: ZIP temporário + arquivo cifrado
        self._patch(slug, rid, source_bytes=total, progress={"done": 0, "total": len(entries)}, skipped=len(skipped),
                    warnings=warnings, parts=sorted({e.part for e in entries}))
        if not encrypted:
            part = self._slug_dir(slug) / f".{rid}.part"
            manifest = self._write_archive(slug, rid, site_entries, store_info, skipped, part)
            final = self._zip_path(slug, rid)
            os.replace(part, final)
            _chmod(final, 0o600)
            sha, size = _sha256_file(final)
            self._patch(slug, rid, size_bytes=size, zip_sha256=sha, files=len(manifest["files"]), skipped=len(skipped), parts=manifest["parts"],
                        progress={"done": len(manifest["files"]), "total": len(manifest["files"])}, protection=None)
            self._set(slug, rid, VALIDANDO)
            self._finish_validation(slug, rid, None)
            return
        tmp = self._tmp_dir(rid)
        plain = tmp / "backup.zip"
        manifest = self._write_archive(slug, rid, site_entries, store_info, skipped, plain)
        before = validate_archive(plain, slug, max_files=self.max_files, max_bytes=self.max_bytes)    # confere o texto em claro ANTES de cifrar
        if not before.ok:
            raise BackupError("BACKUP_INVALIDO", before.view(self._now())["message"])
        plain_sha, plain_size = _sha256_file(plain)
        self._hook("before_encrypt")
        part = self._slug_dir(slug) / f".{rid}.part"
        info = bcrypto.encrypt_file(plain, part, password, self.kdf)
        self._hook("after_encrypt")
        final = self._enc_path(slug, rid)
        os.replace(part, final)
        _chmod(final, 0o600)
        shutil.rmtree(tmp, ignore_errors=True)                                          # o ZIP em claro some assim que o cifrado existe
        sha, size = _sha256_file(final)
        protection = {"state": "CRIPTOGRAFADO", "format": info["format"], "version": info["version"], "cipher": info["cipher"], "kdf": info["kdf"],
                      "plain_sha256": plain_sha, "plain_size": plain_size, "content_verified_at": None}
        self._patch(slug, rid, size_bytes=size, zip_sha256=sha, files=len(manifest["files"]), skipped=len(skipped), parts=manifest["parts"],
                    progress={"done": len(manifest["files"]), "total": len(manifest["files"])}, protection=protection)
        self._set(slug, rid, VALIDANDO)
        self._finish_validation(slug, rid, password, before)

    def _write_archive(self, slug: str, rid: str, site_entries: list[Entry], store_info: dict | None, skipped: list[str], target: Path) -> dict:
        created = self._now()
        stamp = time.gmtime(max(created, 315532800))[:6]
        manifest_files: list[dict] = []
        done = 0

        def add(zf: zipfile.ZipFile, entry: Entry) -> None:
            nonlocal done
            digest, size = hashlib.sha256(), 0
            info = zipfile.ZipInfo(entry.arc, date_time=stamp)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            with open(entry.path, "rb") as src, zf.open(info, "w", force_zip64=True) as dst:
                while chunk := src.read(1 << 20):
                    size += len(chunk)
                    if size > self.max_bytes:
                        raise BackupError("LIMITE_TAMANHO")
                    digest.update(chunk)
                    dst.write(chunk)
            manifest_files.append({"path": entry.arc, "size": size, "sha256": digest.hexdigest(), "part": entry.part})
            done += 1
            self._hook("during_create", done)
            if done % 25 == 0:
                self._patch(slug, rid, progress={"done": done, "total": self._load(slug, rid)["progress"]["total"]})

        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as handle:
            with zipfile.ZipFile(handle, "w", zipfile.ZIP_DEFLATED) as zf:
                for entry in site_entries:
                    add(zf, entry)
                with catalog.Catalog._lock:               # catálogo consistente: ninguém edita enquanto copiamos
                    fresh, store_info = self._gather_catalog(slug, skipped)
                    for entry in fresh:
                        add(zf, entry)
                    if store_info is not None:
                        blob = json.dumps(store_info, ensure_ascii=False).encode("utf-8")
                        zinfo = zipfile.ZipInfo("catalog/store_info.json", date_time=stamp)
                        zinfo.compress_type, zinfo.external_attr = zipfile.ZIP_DEFLATED, 0o600 << 16
                        zf.writestr(zinfo, blob)
                        manifest_files.append({"path": "catalog/store_info.json", "size": len(blob), "sha256": hashlib.sha256(blob).hexdigest(), "part": "catalog"})
                manifest = {"format": FORMAT, "version": FORMAT_VERSION, "id": rid, "slug": slug, "created_at": created,
                            "created_by": "painel-admin", "parts": sorted({f["part"] for f in manifest_files}), "files": manifest_files,
                            "totals": {"files": len(manifest_files), "bytes": sum(f["size"] for f in manifest_files)}}
                zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
            handle.flush()
            os.fsync(handle.fileno())
        return manifest

    # ----- pasta temporária privada (único lugar onde pode existir texto em claro de um backup protegido, e só durante a operação)
    def _tmp_dir(self, token: str) -> Path:
        _ensure_dir(self.tmp_root)
        folder = self.tmp_root / token
        _ensure_dir(folder)
        return folder

    @staticmethod
    def _crypto_validation(err: bcrypto.CryptoError) -> Validation:
        code = {"ARQUIVO_ALTERADO": "ARQUIVO_ALTERADO", "SENHA_INCORRETA": "SENHA_INCORRETA", "VERSAO_NAO_SUPORTADA": "VERSAO_INCOMPATIVEL",
                "FORMATO_INVALIDO": "FORMATO_PROTEGIDO_INVALIDO", "PARAMETROS_INVALIDOS": "FORMATO_PROTEGIDO_INVALIDO"}.get(err.code, "ARQUIVO_CORROMPIDO")
        return Validation(False, code, "falha ao abrir o arquivo protegido", mode="protegida")

    def _max_plain(self) -> int:
        return self.max_bytes + MAX_MANIFEST_BYTES + 16 * 1024 * 1024

    def _check_file(self, rec: dict) -> Validation | None:
        """Nível 1 (sem senha): o arquivo existe, tem o tamanho e a impressão digital registrados e o cabeçalho do formato."""
        path = self._file_path(rec)
        try:
            info = os.lstat(path)
        except OSError:
            return Validation(False, "ARQUIVO_NAO_ENCONTRADO", mode="arquivo")
        if not stat.S_ISREG(info.st_mode) or info.st_size == 0:
            return Validation(False, "ARQUIVO_CORROMPIDO", mode="arquivo")
        if rec.get("size_bytes") is not None and info.st_size != rec["size_bytes"]:
            return Validation(False, "ARQUIVO_ALTERADO", "tamanho diferente do registrado", mode="arquivo")
        if rec.get("zip_sha256") and _sha256_file(path)[0] != rec["zip_sha256"]:
            return Validation(False, "ARQUIVO_ALTERADO", "impressão digital diferente da registrada", mode="arquivo")
        if self._is_encrypted(rec) and bcrypto.read_header(path) is None:
            return Validation(False, "FORMATO_PROTEGIDO_INVALIDO", mode="arquivo")
        return None

    def _open_plain(self, rec: dict, password: object, tmp: Path) -> tuple[Path | None, Validation | None]:
        """Devolve (caminho do ZIP em claro, None) ou (None, motivo). Legado: o próprio ZIP. Protegido: descriptografa para `tmp`."""
        bad = self._check_file(rec)
        if bad is not None:
            return None, bad
        if not self._is_encrypted(rec):
            return self._zip_path(rec["slug"], rec["id"]), None
        plain = tmp / "backup.zip"
        try:
            bcrypto.decrypt_file(self._enc_path(rec["slug"], rec["id"]), plain, password, max_plain=self._max_plain())
        except bcrypto.CryptoError as err:
            return None, self._crypto_validation(err)
        expected = (rec.get("protection") or {}).get("plain_sha256")
        if expected and _sha256_file(plain)[0] != expected:
            plain.unlink(missing_ok=True)
            return None, Validation(False, "ARQUIVO_ALTERADO", "conteúdo diferente do registrado", mode="protegida")
        return plain, None

    def _validate_record(self, rec: dict, password: object) -> Validation:
        """Valida um backup existente. Com senha: abre o arquivo protegido e confere o conteúdo; sem senha: só o arquivo."""
        slug = rec["slug"]
        if not self._is_encrypted(rec):
            return validate_archive(self._zip_path(slug, rec["id"]), slug, expected_size=rec["size_bytes"], expected_sha=rec["zip_sha256"],
                                    max_files=self.max_files, max_bytes=self.max_bytes)
        if password is None:
            bad = self._check_file(rec)
            return bad if bad is not None else Validation(True, files=rec["files"], bytes=rec["source_bytes"], mode="arquivo")
        tmp = self._tmp_dir(secrets.token_hex(6))
        try:
            plain, bad = self._open_plain(rec, password, tmp)
            if bad is not None:
                return bad
            result = validate_archive(plain, slug, max_files=self.max_files, max_bytes=self.max_bytes)
            result.mode = "protegida"
            return result
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def _finish_validation(self, slug: str, rid: str, password: object, before: Validation | None = None) -> None:
        rec = self._load(slug, rid)
        if self._is_encrypted(rec):
            result = self._verify_after_create(rec, password, before)
        else:
            result = self._validate_record(rec, None)
        view = result.view(self._now())
        if result.ok:
            fields: dict = {"validation": view, "error_code": "", "error_message": "", "next_action": ""}
            if self._is_encrypted(rec):
                fields["protection"] = {**rec["protection"], "content_verified_at": self._now()}
            self._set(slug, rid, CONCLUIDO, **fields)
        else:
            self._set(slug, rid, INVALIDO, validation=view, error_code="BACKUP_INVALIDO", error_message=view["message"],
                      next_action=ERRORS["BACKUP_INVALIDO"][1])

    def _verify_after_create(self, rec: dict, password: object, before: Validation | None) -> Validation:
        """Depois de cifrar: abre o arquivo cifrado inteiro (cada bloco é autenticado) e confere que o texto em claro é idêntico ao que foi validado."""
        bad = self._check_file(rec)
        if bad is not None:
            return bad
        try:
            total, digest = bcrypto.verify_file(self._enc_path(rec["slug"], rec["id"]), password)
        except bcrypto.CryptoError as err:
            return self._crypto_validation(err)
        prot = rec["protection"]
        if digest != prot.get("plain_sha256") or total != prot.get("plain_size"):
            return Validation(False, "ARQUIVO_ALTERADO", "o arquivo cifrado não abre igual ao original", mode="protegida")
        return Validation(True, files=before.files if before else rec["files"], bytes=before.bytes if before else rec["source_bytes"],
                          manifest=before.manifest if before else {}, mode="protegida")

    # ----- verificar integridade sob demanda
    def validate(self, slug: str, rid: str, password: object = None) -> dict:
        slug = self._require_known(slug)
        rec = self._load(slug, rid)
        if rec["kind"] not in KINDS_BACKUP or rec["state"] not in (CONCLUIDO, INVALIDO):
            raise BackupError("ESTADO_INVALIDO", "Só dá para verificar um backup já criado.")
        use_password = None
        if self._is_encrypted(rec) and isinstance(password, str) and password:
            self._check_password(password)
            use_password = password
        result = self._validate_record(rec, use_password)
        view = result.view(self._now())
        full = result.mode in ("completa", "protegida")
        if result.ok:
            if full and rec["state"] == INVALIDO:
                target = CONCLUIDO
            else:
                target = rec["state"]                      # só o arquivo (sem senha) não "perdoa" um backup já marcado inválido
        else:
            target = INVALIDO
        fields: dict = {"validation": view}
        if not result.ok:
            fields.update(error_code="BACKUP_INVALIDO", error_message=view["message"], next_action=ERRORS["BACKUP_INVALIDO"][1])
        elif full:
            fields.update(error_code="", error_message="", next_action="")
            if self._is_encrypted(rec):
                fields["protection"] = {**rec["protection"], "content_verified_at": self._now()}
        return self.view(self._set(slug, rid, target, **fields))

    # ----- listar
    def list(self, slug: str) -> dict:
        slug = self._require_known(slug)
        records = self._records(slug)
        backups = [self.view(r) for r in records if r["kind"] in KINDS_BACKUP and r["state"] != EXCLUIDO]
        history = [self.view(r) for r in records][:HISTORY_LIMIT]
        return {"slug": slug, "backups": backups, "history": history, "storage": self.storage(slug), "reminder": self.reminder(slug),
                "active": self._active_view(slug), "protection": self.protection_status(), "store": self._store_state(slug)}

    def _store_state(self, slug: str) -> dict:
        store = _read_json(self.catalogs / slug / "store.json")
        return {"exists": bool(store), "pending": bool(store.get("pending")), "name": str(store.get("name", ""))[:60],
                "site_exists": (self.sites / slug / "index.html").is_file()}

    def _active_view(self, slug: str) -> dict | None:
        rid = self._active.get(slug)
        try:
            return self.view(self._load(slug, rid)) if rid else None
        except BackupError:
            return None

    def get(self, slug: str, rid: str) -> dict:
        return self.view(self._load(self._require_known(slug), rid))

    # ----- exportar / importar
    def prepare_export(self, slug: str, rid: str) -> tuple[Path, int, str]:
        slug = self._require_known(slug)
        rec = self._load(slug, rid)
        if rec["state"] == EXCLUIDO:
            raise BackupError("NAO_ENCONTRADO")             # backup excluído: o arquivo não existe mais
        if rec["kind"] not in KINDS_BACKUP or rec["state"] != CONCLUIDO:
            raise BackupError("AINDA_NAO_VALIDADO")
        fresh = self.validate(slug, rid)                    # nunca exporta sem conferir de novo (arquivo; legado: conteúdo inteiro)
        if fresh["state"] != CONCLUIDO:
            raise BackupError("BACKUP_INVALIDO", fresh["error_message"])
        path = self._file_path(rec)
        stamp = time.strftime("%Y%m%d-%H%M", time.gmtime(rec["started_at"]))
        ext = bcrypto.FILE_EXTENSION if self._is_encrypted(rec) else ".zip"
        return path, path.stat().st_size, f"cdbs-backup-{slug}-{stamp}-{rid[:8]}{ext}"

    def record_export(self, slug: str, rid: str, ok: bool, sent: int) -> None:
        """Registra que o arquivo foi ENTREGUE ao navegador. Isso não prova que existe cópia externa."""
        with self._rec_lock:
            rec = self._load(slug, rid)
            rec["exports"] = (rec["exports"] + [{"at": self._now(), "ok": bool(ok), "bytes": int(sent)}])[-20:]
            self._save(rec)
        self._event(slug, "exportacao" if ok else "exportacao_falhou", f"Backup {rid[:8]}: {'entregue ao navegador' if ok else 'falhou'} ({sent} bytes).")

    def import_archive(self, slug: str, reader: BinaryIO, length: int, password: object = None) -> dict:
        """Recebe um backup exportado antes. Protegido (.cdbk): exige a senha de recuperação DESTE CDBS e abre o arquivo inteiro.
        ZIP comum (legado): aceito e rotulado como LEGADO. Só entra o que for íntegro e desta mesma loja."""
        slug = self._require_known(slug)
        if length <= 0 or length > self.max_bytes + MAX_MANIFEST_BYTES:
            raise BackupError("ARQUIVO_MUITO_GRANDE")
        with self._guard:
            if self._active.get(slug):
                raise BackupError("EM_ANDAMENTO")
            rec = self._new_record(slug, "importado", state=VALIDANDO)
            self._active[slug] = rec["id"]
        rid = rec["id"]
        part = self._slug_dir(slug) / f".{rid}.part"
        tmp = self.tmp_root / rid
        try:
            self._need_space(slug, length * 3)
            remaining, head = length, b""
            fd = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as out:
                while remaining > 0:
                    chunk = reader.read(min(1 << 20, remaining))
                    if not chunk:
                        raise BackupError("ARQUIVO_MUITO_GRANDE", "Envio incompleto.", status=400)
                    if len(head) < 8:
                        head += chunk[:8]
                    out.write(chunk)
                    remaining -= len(chunk)
                out.flush()
                os.fsync(out.fileno())
            encrypted = bcrypto.looks_encrypted(head)
            if encrypted:
                self._check_password(password)                      # a senha tem de ser a deste CDBS
                header = bcrypto.read_header(part)
                if header is None:
                    raise BackupError("BACKUP_INVALIDO", VALIDATION_MESSAGES["FORMATO_PROTEGIDO_INVALIDO"])
                tmp = self._tmp_dir(rid)
                plain = tmp / "backup.zip"
                try:
                    bcrypto.decrypt_file(part, plain, password, max_plain=self._max_plain())
                except bcrypto.CryptoError as err:
                    if err.code == "SENHA_INCORRETA":
                        raise BackupError("OUTRA_SENHA") from None   # a senha deste CDBS já foi conferida acima
                    result = self._crypto_validation(err)
                else:
                    result = validate_archive(plain, slug, max_files=self.max_files, max_bytes=self.max_bytes)
                    result.mode = "protegida"
                    plain_sha, plain_size = _sha256_file(plain)
            else:
                result = validate_archive(part, slug, max_files=self.max_files, max_bytes=self.max_bytes)
            view = result.view(self._now())
            if not result.ok:
                part.unlink(missing_ok=True)
                self._set(slug, rid, FALHOU, validation=view, error_code="BACKUP_INVALIDO", error_message=view["message"],
                          next_action="Envie um backup exportado por esta loja no CDBS.")
                raise BackupError("BACKUP_INVALIDO", view["message"], record=self.view(self._load(slug, rid)))
            final = self._enc_path(slug, rid) if encrypted else self._zip_path(slug, rid)
            os.replace(part, final)
            _chmod(final, 0o600)
            sha, size = _sha256_file(final)
            manifest = result.manifest
            protection = None
            if encrypted:
                protection = {"state": "CRIPTOGRAFADO", "format": header["format"], "version": header["version"], "cipher": header["cipher"],
                              "kdf": header["kdf"], "plain_sha256": plain_sha, "plain_size": plain_size, "content_verified_at": self._now()}
            return self.view(self._set(slug, rid, CONCLUIDO, validation=view, size_bytes=size, zip_sha256=sha, files=result.files,
                                       source_bytes=result.bytes, parts=manifest.get("parts", []), protection=protection,
                                       imported_from=str(manifest.get("id", ""))[:12]))
        except BackupError as err:
            part.unlink(missing_ok=True)
            self._fail(slug, rid, err)
            raise
        except Exception:                                                    # noqa: BLE001
            part.unlink(missing_ok=True)
            self._fail(slug, rid, BackupError("ERRO_DESCONHECIDO"))
            raise BackupError("ERRO_DESCONHECIDO") from None
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
            self._release(slug, rid)

    # ----- restauração
    def _restore_mode(self, slug: str, rec: dict) -> str:
        """'recuperar_loja' = o backup tem catálogo e a loja não existe mais; senão, restauração normal."""
        has_catalog = "catalog" in (rec.get("parts") or [])
        return "recuperar_loja" if has_catalog and not (self.catalogs / slug / "store.json").is_file() else "restaurar"

    def restore_summary(self, slug: str, rid: str) -> dict:
        slug = self._require_known(slug)
        rec = self._load(slug, rid)
        if rec["kind"] not in KINDS_BACKUP or rec["state"] != CONCLUIDO:
            raise BackupError("AINDA_NAO_VALIDADO")
        fresh = self.validate(slug, rid)                      # nível arquivo (sem senha); o conteúdo é conferido ao restaurar, com a senha
        if fresh["state"] != CONCLUIDO:
            raise BackupError("BACKUP_INVALIDO", fresh["error_message"])
        rec = self._load(slug, rid)
        encrypted = self._is_encrypted(rec)
        status = self.protection_status()
        current_site = dir_size(self.sites / slug)
        store_exists = (self.catalogs / slug / "store.json").is_file()
        current_catalog = dir_size(self.catalogs / slug) if store_exists else 0
        will_safety = bool(current_site or current_catalog)
        needs_password = encrypted or (will_safety and status["available"])
        if encrypted and not status["available"]:
            raise BackupError("PROTECAO_INDISPONIVEL")
        if needs_password and not status["configured"]:
            raise BackupError("PROTECAO_NAO_CONFIGURADA")
        mode = self._restore_mode(slug, rec)
        plain_extra = (rec.get("protection") or {}).get("plain_size", 0) if encrypted else 0
        needed = rec["source_bytes"] + plain_extra + current_site * 2 + current_catalog * 2
        free = self._free(slug)
        code = secrets.token_urlsafe(18)
        with self._guard:
            self._confirm = {k: v for k, v in self._confirm.items() if v["exp"] > self._clock()}
            self._confirm[code] = {"kind": "restore", "slug": slug, "rid": rid, "exp": self._clock() + CONFIRM_TTL, "mode": mode,
                                   "needs_password": needs_password}
        store = _read_json(self.catalogs / slug / "store.json")
        warning = ("Os arquivos atuais desta loja serão SUBSTITUÍDOS pelos do backup. Antes disso o CDBS guarda um backup de segurança do estado atual. A senha do cliente não muda."
                   if mode == "restaurar" else
                   "Esta loja foi REMOVIDA. Ela será recriada com o conteúdo do backup, porém DESATIVADA e SEM senha: o site e o catálogo ficam invisíveis ao público até você gerar uma nova senha para o cliente. Senhas e sessões antigas nunca voltam.")
        return {
            "store": slug, "store_name": str(store.get("name", ""))[:60] or slug, "mode": mode, "recreate_store": mode == "recuperar_loja",
            "backup": {"id": rid, "short_id": rid[:8], "created_at": rec["started_at"], "size_bytes": rec["size_bytes"], "files": rec["files"], "parts": rec["parts"], "kind": rec["kind"],
                       "protection": self.view(rec)["protection"]},
            "current": {"site_bytes": current_site, "catalog_bytes": current_catalog, "has_site": (self.sites / slug / "index.html").is_file(), "has_catalog": current_catalog > 0},
            "will_create_safety_backup": will_safety, "safety_backup_protected": status["available"], "needs_password": needs_password,
            "space": {"needed_bytes": needed + SPACE_MARGIN, "free_bytes": free, "enough": None if free is None else free >= needed + SPACE_MARGIN},
            "warning": warning,
            "confirm_token": code, "confirm_text": slug, "expires_in": CONFIRM_TTL,
        }

    def start_restore(self, slug: str, rid: str, confirm_token: object, confirm_slug: object, password: object = None,
                      confirm_recreate: object = False) -> tuple[dict, bool]:
        slug = self._require_known(slug)
        rec = self._load(slug, rid)
        with self._guard:                                          # confere o código SEM gastá-lo: erro de senha não obriga a recomeçar
            info = self._confirm.get(confirm_token) if isinstance(confirm_token, str) else None
            if (not info or info.get("kind") != "restore" or info["slug"] != slug or info["rid"] != rid or info["exp"] < self._clock()
                    or not isinstance(confirm_slug, str) or confirm_slug.strip() != slug):
                raise BackupError("CONFIRMACAO")
        if rec["kind"] not in KINDS_BACKUP or rec["state"] != CONCLUIDO:
            raise BackupError("AINDA_NAO_VALIDADO")
        if info["mode"] == "recuperar_loja" and confirm_recreate is not True:
            raise BackupError("CONFIRMACAO", "Falta confirmar que a loja será recriada desativada e sem senha.")
        if self._restore_mode(slug, rec) != info["mode"]:
            with self._guard:
                self._confirm.pop(confirm_token, None)
            raise BackupError("SITUACAO_MUDOU")
        if info["needs_password"]:
            self._check_password(password)
        with self._guard:
            if self._confirm.pop(confirm_token, None) is None:
                raise BackupError("CONFIRMACAO")
            if self._active.get(slug):
                raise BackupError("EM_ANDAMENTO", record=self.view(self._load(slug, self._active[slug])))
            op = self._new_record(slug, "restauracao", source_backup=rid)
            self._active[slug] = op["id"]
        secret = password if info["needs_password"] else None
        mode = info["mode"]
        self._spawn(op["id"], lambda: self._run_restore(slug, op["id"], rid, secret, mode))
        return self.view(op), True

    def _journal_path(self, slug: str) -> Path:
        return self._slug_dir(slug) / "restore-journal.json"

    def _run_restore(self, slug: str, opid: str, rid: str, password: object, mode: str = "restaurar") -> None:
        journal_written = False
        try:
            journal_written = self._do_restore(slug, opid, rid, password, mode)
        except BackupError as err:
            self._restore_failed(slug, opid, err, journal_written)
        except bcrypto.CryptoError:
            self._restore_failed(slug, opid, BackupError("BACKUP_INVALIDO", "Não foi possível abrir o backup protegido."), journal_written)
        except Exception:                                                    # noqa: BLE001
            self._restore_failed(slug, opid, BackupError("ERRO_DESCONHECIDO"), journal_written)
        finally:
            if _read_json(self._journal_path(slug)).get("opid") != opid:     # se o desfazer não terminou, a pasta com o estado antigo é preservada
                self._cleanup_tmp(opid)
            self._release(slug, opid)

    def _restore_failed(self, slug: str, opid: str, err: BackupError, _journal_written: bool) -> None:
        rec = self._load(slug, opid)
        if rec["state"] in (CONCLUIDO, FALHOU, REVERTIDO):
            return
        reverted = rec["state"] in (APLICANDO, VERIFICANDO)
        fields = {"error_code": err.code, "error_message": str(err), "next_action": err.next_action}
        if reverted:
            fields["next_action"] = "A versão anterior foi recolocada. Nada foi perdido."
        self._set(slug, opid, REVERTIDO if reverted else FALHOU, **fields)

    def _mark_invalid(self, slug: str, rid: str, failed: dict) -> None:
        rec = self._load(slug, rid)
        if rec["state"] == CONCLUIDO:
            self._set(slug, rid, INVALIDO, validation=failed, error_code="BACKUP_INVALIDO", error_message=failed["message"],
                      next_action=ERRORS["BACKUP_INVALIDO"][1])

    def _do_restore(self, slug: str, opid: str, rid: str, password: object, mode: str) -> bool:
        rec = self._load(slug, rid)
        self._set(slug, opid, VALIDANDO)
        tmp = self._tmp_dir(opid)
        plain, bad = self._open_plain(rec, password, tmp)          # protegido: descriptografa e autentica TUDO antes de qualquer troca
        if bad is not None:
            if bad.code == "SENHA_INCORRETA":
                raise BackupError("OUTRA_SENHA")
            failed = bad.view(self._now())
            self._mark_invalid(slug, rid, failed)
            raise BackupError("BACKUP_INVALIDO", failed["message"])
        result = validate_archive(plain, slug, max_files=self.max_files, max_bytes=self.max_bytes)
        if not result.ok:
            failed = result.view(self._now())
            self._mark_invalid(slug, rid, failed)
            raise BackupError("BACKUP_INVALIDO", failed["message"])
        files = result.manifest["files"]
        has_site = any(f["path"].startswith("site/") for f in files)
        catalog_in_backup = any(f["path"].startswith("catalog/") for f in files)
        store_exists = (self.catalogs / slug / "store.json").is_file()
        recreate = mode == "recuperar_loja"
        if (recreate and store_exists) or (not recreate and catalog_in_backup and not store_exists):
            raise BackupError("SITUACAO_MUDOU")                    # a loja foi criada/removida entre o resumo e agora
        has_catalog_part = catalog_in_backup
        current_bytes = dir_size(self.sites / slug) + (dir_size(self.catalogs / slug) if store_exists else 0)
        plain_size = plain.stat().st_size if self._is_encrypted(rec) else 0
        warnings = self._need_space(slug, result.bytes + plain_size + current_bytes * 2)      # ANTES de mexer em qualquer coisa
        self._set(slug, opid, PREPARANDO)

        safety_id = None
        if current_bytes:
            safety = self._new_record(slug, "seguranca", origin=f"antes de restaurar {rid[:8]}")
            safety_password = password if bcrypto.available() else None
            if bcrypto.available() and safety_password is None:
                raise BackupError("FALHA_BACKUP_SEGURANCA", f"{ERRORS['FALHA_BACKUP_SEGURANCA'][0]} (falta a senha de recuperação)")
            try:
                self._do_backup(slug, safety["id"], safety_password)
            except BackupError as err:
                self._fail(slug, safety["id"], err)
                self._cleanup_tmp(safety["id"])
                if err.code != "SEM_DADOS":          # só havia arquivos ocultos/segredos: não há o que proteger
                    raise BackupError("FALHA_BACKUP_SEGURANCA", f"{ERRORS['FALHA_BACKUP_SEGURANCA'][0]} ({err})") from None
            except Exception:                                                # noqa: BLE001
                self._fail(slug, safety["id"], BackupError("FALHA_CRIACAO"))
                self._cleanup_tmp(safety["id"])
                raise BackupError("FALHA_BACKUP_SEGURANCA") from None
            else:
                if self._load(slug, safety["id"])["state"] != CONCLUIDO:
                    raise BackupError("FALHA_BACKUP_SEGURANCA")
                safety_id = safety["id"]
                self._patch(slug, opid, safety_backup=safety_id)
                if not self._is_encrypted(self._load(slug, safety_id)):
                    warnings = warnings + ["O backup de segurança ficou SEM criptografia porque a biblioteca de criptografia não está instalada."]

        # Quando o catálogo muda, a trava do catálogo fica segurada da troca até a conferência final: uma gravação do cliente
        # não pode entrar no meio (faria a conferência falhar à toa) e ninguém cria uma loja com este nome durante a recuperação.
        # É uma trava reentrante; quem tentar gravar espera alguns instantes.
        gate = catalog.Catalog._lock if (has_catalog_part or recreate) else contextlib.nullcontext()
        try:
            new_dir = tmp / "new"
            self._extract(plain, new_dir, files)
            self._hook("after_extract")
            ok, why = verify_tree(new_dir / "site", files, prefix="site/") if has_site else (True, "")
            ok2, why2 = verify_tree(new_dir / "catalog" / "media", files, prefix="catalog/media/") if has_catalog_part else (True, "")
            if not (ok and ok2):
                raise BackupError("VERIFICACAO_FALHOU", "Os arquivos preparados não conferem com o backup. Nada foi alterado.")
            with gate:
                self._set(slug, opid, APLICANDO, warnings=warnings + (["A loja foi recriada DESATIVADA e sem senha: gere uma nova senha para ativá-la."] if recreate else []))
                journal = {"opid": opid, "slug": slug, "parts": []}
                self._apply(slug, opid, tmp, new_dir, journal, has_site, has_catalog_part, files, recreate)
                self._set(slug, opid, VERIFICANDO)
                self._hook("before_verify")
                self._verify_applied(slug, files, has_site, has_catalog_part, plain, recreate)
        except BaseException as exc:
            with gate:
                self._rollback(slug, opid)
            self._cleanup_tmp(opid)
            if isinstance(exc, BackupError):
                raise
            raise BackupError("ERRO_DESCONHECIDO") from None
        self._journal_clear(slug)
        self._cleanup_tmp(opid)
        self._set(slug, opid, CONCLUIDO, restored={"site_files": sum(1 for f in files if f["path"].startswith("site/")) if has_site else 0,
                                                    "catalog_files": sum(1 for f in files if f["path"].startswith("catalog/")) if has_catalog_part else 0,
                                                    "catalog_skipped": False, "recreated_store": recreate, "pending_password": recreate, "verified": True},
                  error_code="", error_message="", next_action=("Gere uma nova senha para o cliente em “Catálogo dos clientes” para ativar a loja." if recreate else ""))
        return True

    def _extract(self, zpath: Path, dest: Path, files: list[dict]) -> None:
        _ensure_dir(dest, 0o700)
        base = dest.resolve()
        with zipfile.ZipFile(zpath) as archive:
            for item in files:
                arc = item["path"]
                target = (dest / arc).resolve()
                if base not in target.parents:                              # proteção contra ZIP Slip
                    raise BackupError("BACKUP_INVALIDO", VALIDATION_MESSAGES["CAMINHO_INSEGURO"])
                target.parent.mkdir(parents=True, exist_ok=True)
                digest, size = hashlib.sha256(), 0
                with archive.open(arc) as src, open(target, "xb") as out:
                    while chunk := src.read(1 << 20):
                        size += len(chunk)
                        if size > item["size"]:
                            raise BackupError("BACKUP_INVALIDO", VALIDATION_MESSAGES["HASH_DIVERGENTE"])
                        digest.update(chunk)
                        out.write(chunk)
                if size != item["size"] or digest.hexdigest() != item["sha256"]:
                    raise BackupError("BACKUP_INVALIDO", VALIDATION_MESSAGES["HASH_DIVERGENTE"])
                _chmod(target, 0o644 if arc.startswith("site/") else 0o600)
        (dest / "catalog" / "media").mkdir(parents=True, exist_ok=True)       # catálogo sem fotos também é válido
        (dest / "site").mkdir(parents=True, exist_ok=True)

    # ----- troca com diário (permite voltar mesmo se o processo morrer no meio)
    def _journal_write(self, slug: str, journal: dict) -> None:
        _write_json_private(self._journal_path(slug), journal)

    def _journal_clear(self, slug: str) -> None:
        self._journal_path(slug).unlink(missing_ok=True)

    def _store_from_info(self, slug: str, info: dict, products_time: int) -> dict:
        """Loja recriada: SEM senha, SEM sessões antigas (época nova) e PENDENTE (invisível ao público)."""
        name = str(info.get("name", "")).strip()[:60] or slug
        try:
            whatsapp = catalog.normalize_whatsapp(info.get("whatsapp", ""))
        except catalog.CatalogError:
            whatsapp = ""
        created = info.get("created")
        return {"slug": slug, "name": name, "whatsapp": whatsapp, "password": {}, "pending": True, "epoch": products_time,
                "created": int(created) if isinstance(created, int) and not isinstance(created, bool) else products_time, "recovered_at": products_time}

    def _apply(self, slug: str, opid: str, tmp: Path, new_dir: Path, journal: dict, has_site: bool, has_catalog: bool, files: list[dict],
               recreate: bool = False) -> None:
        site_target = self.sites / slug
        cat_dir = self.catalogs / slug
        media_target = cat_dir / "media"
        products_target = cat_dir / "products.json"
        old_site, old_media, old_products, old_catdir = tmp / "old-site", tmp / "old-media", tmp / "old-products.json", tmp / "old-catalogdir"
        # Os itens do diário são gravados ANTES de cada passo; o desfazer olha o que existe no disco.
        if has_site:
            journal["parts"].append({"part": "site", "target": str(site_target), "old": str(old_site), "had_old": site_target.exists()})
        if has_catalog and not recreate:
            journal["parts"].append({"part": "media", "target": str(media_target), "old": str(old_media), "had_old": media_target.exists()})
            journal["parts"].append({"part": "products", "target": str(products_target), "old": str(old_products), "had_old": products_target.exists()})
        if recreate:
            journal["parts"].append({"part": "catalogdir", "target": str(cat_dir), "old": str(old_catdir), "had_old": cat_dir.exists()})
        journal["discard"] = str(tmp / "discard")
        self._journal_write(slug, journal)
        _ensure_dir(tmp / "discard")
        self._hook("before_apply")
        build = None
        if recreate:                                                    # a loja inteira é montada ao lado e entra numa única troca
            build = tmp / "catalogdir"
            _ensure_dir(build)
            os.rename(new_dir / "catalog" / "media", build / "media")
            restored = _read_json(new_dir / "catalog" / "products.json")
            restored["rev"] = int(restored.get("rev", 0) or 0) + 1
            restored["updated"] = self._now()
            restored.setdefault("products", [])
            _write_json_private(build / "products.json", restored)
            info = _read_json(new_dir / "catalog" / "store_info.json")
            _write_json_private(build / "store.json", self._store_from_info(slug, info, self._now()))    # por último: sem store.json não existe loja
        with self._maintenance(slug):                                   # visitante recebe "volte já" (503), nunca 404 nem arquivo parcial
            if has_site:
                if site_target.exists():
                    os.rename(site_target, old_site)
                self._hook("during_apply", "site-old-moved")
                os.rename(new_dir / "site", site_target)
            if recreate:
                self.catalogs.mkdir(parents=True, exist_ok=True)
                with catalog.Catalog._lock:                              # a mesma trava do "criar loja": ninguém cria a loja entre a conferência e a troca
                    if (cat_dir / "store.json").is_file():
                        raise BackupError("LOJA_JA_EXISTE")             # alguém criou a loja agora há pouco: NÃO sobrescreve (o desfazer recoloca o site)
                    if cat_dir.exists():
                        os.rename(cat_dir, old_catdir)
                    self._hook("during_apply", "catalog-old-moved")
                    os.rename(build, cat_dir)
            elif has_catalog:
                with catalog.Catalog._lock:
                    if media_target.exists():
                        os.rename(media_target, old_media)
                    os.rename(new_dir / "catalog" / "media", media_target)
                    if products_target.exists():
                        shutil.copy2(products_target, old_products)
                        current_rev = int(_read_json(products_target).get("rev", 0) or 0)
                    else:
                        current_rev = 0
                    self._hook("during_apply", "catalog-media-swapped")
                    restored = _read_json(new_dir / "catalog" / "products.json")
                    restored["rev"] = max(current_rev, int(restored.get("rev", 0) or 0)) + 1     # painéis abertos percebem a mudança
                    restored["updated"] = self._now()
                    _write_json_private(products_target, restored)
                    _chmod(products_target, 0o600)

    def _verify_applied(self, slug: str, files: list[dict], has_site: bool, has_catalog: bool, plain: Path, recreate: bool = False) -> None:
        if has_site:
            ok, _ = verify_tree(self.sites / slug, files, prefix="site/")
            if not ok:
                raise BackupError("VERIFICACAO_FALHOU")
            if not (self.sites / slug / "index.html").is_file():
                raise BackupError("VERIFICACAO_FALHOU")
        if has_catalog:
            with catalog.Catalog._lock:
                ok, _ = verify_tree(self.catalogs / slug / "media", files, prefix="catalog/media/")
                if not ok:
                    raise BackupError("VERIFICACAO_FALHOU")
                expected = next((f for f in files if f["path"] == "catalog/products.json"), None)
                live = _read_json(self.catalogs / slug / "products.json")
                if expected is not None:
                    with zipfile.ZipFile(plain) as archive:
                        original = json.loads(archive.read("catalog/products.json").decode("utf-8"))
                    if live.get("products") != original.get("products"):
                        raise BackupError("VERIFICACAO_FALHOU")
                if recreate:
                    store = _read_json(self.catalogs / slug / "store.json")
                    if not store.get("pending") or store.get("password"):
                        raise BackupError("VERIFICACAO_FALHOU")          # a loja recuperada DEVE estar pendente e sem senha

    def _rollback(self, slug: str, opid: str) -> None:
        journal = _read_json(self._journal_path(slug))
        if journal.get("opid") == opid:
            self._undo_journal(journal)
            self._journal_clear(slug)

    def _undo_journal(self, journal: dict) -> None:
        """Volta ao estado anterior olhando só o que existe no disco (serve para falha normal e para o reinício)."""
        discard = Path(journal.get("discard", ""))
        if discard.parent.parent != self.tmp_root:
            return                                                           # nunca mexe fora da nossa pasta temporária
        discard.mkdir(parents=True, exist_ok=True)
        lock_cat = catalog.Catalog._lock
        slug = str(journal.get("slug", ""))
        with self._maintenance(slug):
            for part in reversed(journal.get("parts", [])):
                target, old = Path(part["target"]), Path(part["old"])
                kind = part["part"]
                inside = lambda base, item: base in item.parents                  # noqa: E731
                if kind not in ("site", "media", "products", "catalogdir") or not inside(self.tmp_root, old) or not (inside(self.sites, target) or inside(self.catalogs, target)):
                    continue                                                      # diário adulterado: não mexe fora das nossas pastas
                if kind == "products":
                    if old.exists():
                        holder = target.with_name(f".{target.name}.undo")
                        with lock_cat:
                            shutil.copy2(old, holder)
                            os.replace(holder, target)
                    elif not part["had_old"] and target.exists():
                        with lock_cat:
                            target.unlink(missing_ok=True)
                    continue
                if part["had_old"]:
                    if old.exists():                                             # o antigo foi tirado do lugar: devolve
                        if target.exists():
                            os.rename(target, discard / f"{kind}-{secrets.token_hex(3)}")
                        os.rename(old, target)
                elif target.exists() and kind in ("site", "media", "catalogdir"):
                    if kind == "catalogdir":                                         # só tira a loja se for a NOSSA (pendente, marcada); uma loja criada por outra pessoa nunca é removida
                        mine = _read_json(target / "store.json")
                        if not (mine.get("pending") and mine.get("recovered_at")):
                            continue
                    os.rename(target, discard / f"{kind}-{secrets.token_hex(3)}")   # não existia antes: tira o que entrou

    def _cleanup_tmp(self, opid: str) -> None:
        if ID_RE.fullmatch(opid):
            shutil.rmtree(self.tmp_root / opid, ignore_errors=True)

    # ----- recuperação ao iniciar
    def recover(self) -> dict:
        report = {"interrupted": 0, "reverted": 0, "cleaned": 0}
        if self.root.is_dir():
            for folder in sorted(self.root.iterdir()):
                if not (folder.is_dir() and SLUG_RE.fullmatch(folder.name)):
                    continue
                slug = folder.name
                for leftover in folder.glob(".*.part"):
                    leftover.unlink(missing_ok=True)
                journal = _read_json(self._journal_path(slug))
                if journal.get("slug") == slug:
                    try:
                        self._undo_journal(journal)
                        report["reverted"] += 1
                        self._journal_clear(slug)
                        if ID_RE.fullmatch(str(journal.get("opid", ""))):
                            self._cleanup_tmp(journal["opid"])
                    except Exception:                                            # noqa: BLE001 — a inicialização nunca pode cair aqui
                        self._event(slug, "falha_recuperacao", "Não foi possível desfazer por completo uma restauração interrompida. O diário foi mantido.")
                for rec in self._records(slug):
                    if rec["state"] in ACTIVE:
                        restore = rec["kind"] == "restauracao"
                        reverted = restore and rec["state"] in (APLICANDO, VERIFICANDO)
                        state = REVERTIDO if reverted else FALHOU
                        try:
                            self._set(slug, rec["id"], state, error_code="INTERROMPIDO", error_message=ERRORS["INTERROMPIDO"][0],
                                      next_action="A versão anterior foi recolocada." if reverted else ERRORS["INTERROMPIDO"][1], origin="recuperacao")
                            report["interrupted"] += 1
                        except ValueError:
                            pass
        report["cleaned"] = self._sweep_tmp()
        return report

    def _sweep_tmp(self) -> int:
        """Apaga pastas temporárias órfãs (podem conter texto em claro de um backup protegido). Preserva as que algum diário ainda precisa."""
        if not self.tmp_root.is_dir():
            return 0
        needed: set[str] = set()
        if self.root.is_dir():
            for folder in self.root.iterdir():
                if folder.is_dir() and SLUG_RE.fullmatch(folder.name):
                    opid = str(_read_json(self._journal_path(folder.name)).get("opid", ""))
                    if ID_RE.fullmatch(opid):
                        needed.add(opid)
        removed = 0
        for child in self.tmp_root.iterdir():
            if child.is_dir() and ID_RE.fullmatch(child.name) and child.name not in needed:
                shutil.rmtree(child, ignore_errors=True)
                removed += 1
        return removed

    # ----- migrar um backup LEGADO (ZIP sem criptografia) para o formato protegido, sem tocar no original
    def start_protect(self, slug: str, rid: str, password: object) -> tuple[dict, bool]:
        slug = self._require_known(slug)
        rec = self._load(slug, rid)
        if rec["kind"] not in KINDS_BACKUP or rec["state"] != CONCLUIDO:
            raise BackupError("AINDA_NAO_VALIDADO")
        if self._is_encrypted(rec):
            raise BackupError("JA_PROTEGIDO")
        self._check_password(password)
        with self._guard:
            if self._active.get(slug):
                raise BackupError("EM_ANDAMENTO", record=self.view(self._load(slug, self._active[slug])))
            new = self._new_record(slug, "migrado", source_backup=rid, origin=f"protegendo {rid[:8]}")
            self._active[slug] = new["id"]
        self._spawn(new["id"], lambda: self._run_protect(slug, new["id"], rid, password))
        return self.view(new), True

    def _run_protect(self, slug: str, nid: str, rid: str, password: object) -> None:
        try:
            self._do_protect(slug, nid, rid, password)
        except BackupError as err:
            self._fail(slug, nid, err)
        except bcrypto.CryptoError:
            self._fail(slug, nid, BackupError("FALHA_CRIACAO", "Não foi possível proteger o backup com a senha."))
        except Exception:                                                    # noqa: BLE001
            self._fail(slug, nid, BackupError("FALHA_CRIACAO"))
        finally:
            self._cleanup_tmp(nid)
            self._release(slug, nid)

    def _do_protect(self, slug: str, nid: str, rid: str, password: object) -> None:
        src = self._load(slug, rid)
        legacy = self._zip_path(slug, rid)
        before = validate_archive(legacy, slug, expected_size=src["size_bytes"], expected_sha=src["zip_sha256"], max_files=self.max_files, max_bytes=self.max_bytes)
        if not before.ok:
            failed = before.view(self._now())
            self._mark_invalid(slug, rid, failed)
            raise BackupError("BACKUP_INVALIDO", failed["message"])
        warnings = self._need_space(slug, src["size_bytes"] + 4096)
        self._patch(slug, nid, source_bytes=before.bytes, files=before.files, parts=before.manifest.get("parts", []), warnings=warnings,
                    progress={"done": 0, "total": before.files}, imported_from=str(before.manifest.get("id", ""))[:12])
        part = self._slug_dir(slug) / f".{nid}.part"
        info = bcrypto.encrypt_file(legacy, part, password, self.kdf)
        final = self._enc_path(slug, nid)
        os.replace(part, final)
        _chmod(final, 0o600)
        sha, size = _sha256_file(final)
        protection = {"state": "CRIPTOGRAFADO", "format": info["format"], "version": info["version"], "cipher": info["cipher"], "kdf": info["kdf"],
                      "plain_sha256": src["zip_sha256"], "plain_size": src["size_bytes"], "content_verified_at": None}
        self._patch(slug, nid, size_bytes=size, zip_sha256=sha, protection=protection, progress={"done": before.files, "total": before.files})
        self._set(slug, nid, VALIDANDO)
        self._finish_validation(slug, nid, password, before)
        self._event(slug, "protecao_migrada", f"Backup legado {rid[:8]} protegido em {nid[:8]}. O original foi mantido.")

    # ----- exclusão controlada (sempre manual, sempre com confirmação)
    def _is_last_validated(self, slug: str, rec: dict) -> bool:
        if rec["state"] != CONCLUIDO:
            return False
        return not any(r["id"] != rec["id"] and r["kind"] in KINDS_BACKUP and r["state"] == CONCLUIDO for r in self._records(slug))

    def delete_summary(self, slug: str, rid: str) -> dict:
        slug = self._require_known(slug)
        rec = self._load(slug, rid)
        if rec["kind"] not in KINDS_BACKUP or rec["state"] not in (CONCLUIDO, INVALIDO):
            raise BackupError("EXCLUSAO_INVALIDA")
        if self._active.get(slug):
            raise BackupError("BACKUP_EM_USO")
        last = self._is_last_validated(slug, rec)
        code = secrets.token_urlsafe(18)
        with self._guard:
            self._confirm = {k: v for k, v in self._confirm.items() if v["exp"] > self._clock()}
            self._confirm[code] = {"kind": "delete", "slug": slug, "rid": rid, "exp": self._clock() + CONFIRM_TTL}
        view = self.view(rec)
        return {"store": slug, "backup": {"id": rid, "short_id": rid[:8], "started_at": rec["started_at"], "size_bytes": rec["size_bytes"], "kind": rec["kind"],
                                          "state": rec["state"], "protection": view["protection"]},
                "is_last_validated": last, "confirm_text": rid[:8], "confirm_token": code, "expires_in": CONFIRM_TTL,
                "warning": "O arquivo deste backup será APAGADO deste celular e não há como desfazer. O site publicado e o catálogo não são afetados. "
                           "Se não houver outra cópia (por exemplo uma que você baixou), o conteúdo só existirá no site atual.",
                "last_warning": ("Este é o ÚLTIMO backup validado desta loja. Depois de excluí-lo a loja ficará sem nenhum backup válido." if last else "")}

    def delete_backup(self, slug: str, rid: str, confirm_token: object, confirm_text: object, confirm_last: object = False) -> dict:
        slug = self._require_known(slug)
        rec = self._load(slug, rid)
        with self._guard:
            info = self._confirm.get(confirm_token) if isinstance(confirm_token, str) else None
            if (not info or info.get("kind") != "delete" or info["slug"] != slug or info["rid"] != rid or info["exp"] < self._clock()
                    or not isinstance(confirm_text, str) or confirm_text.strip().lower() != rid[:8]):
                raise BackupError("CONFIRMACAO", "Digite o identificador do backup exatamente como aparece no aviso.")
        if rec["kind"] not in KINDS_BACKUP or rec["state"] not in (CONCLUIDO, INVALIDO):
            raise BackupError("EXCLUSAO_INVALIDA")
        if self._is_last_validated(slug, rec) and confirm_last is not True:
            raise BackupError("ULTIMO_VALIDADO")                              # o código continua valendo: o painel reenvia com a confirmação extra
        with self._guard:
            if self._confirm.pop(confirm_token, None) is None:
                raise BackupError("CONFIRMACAO")
            if self._active.get(slug):
                raise BackupError("BACKUP_EM_USO", record=self.view(self._load(slug, self._active[slug])))
            self._active[slug] = rid                                         # impede criar/restaurar/excluir outro ao mesmo tempo
        try:
            return self._delete_core(slug, rid, "painel")
        finally:
            self._release(slug, rid)

    def _delete_core(self, slug: str, rid: str, origin: str) -> dict:
        rec = self._load(slug, rid)
        path = self._file_path(rec)
        size = rec.get("size_bytes") or 0
        self._hook("before_delete")
        try:
            path.unlink(missing_ok=True)
        except OSError:
            self._event(slug, "exclusao_falhou", f"Não foi possível excluir o backup {rid[:8]}.")
            raise BackupError("ERRO_DESCONHECIDO", "Não foi possível apagar o arquivo do backup. Nada foi alterado no site.") from None
        done = self._set(slug, rid, EXCLUIDO, deleted_at=self._now(), error_code="", error_message="", next_action="", origin=origin)
        self._event(slug, "exclusao", f"Backup {rid[:8]} excluído ({size} bytes) — origem: {origin}.")
        return self.view(done)

    # ----- retenção: política guardada e prévia/limpeza MANUAIS. Nenhuma exclusão automática existe (BLOCKED — aguardando autorização)
    def _retention_path(self) -> Path:
        return self.root / "retention.json"

    def retention_get(self) -> dict:
        cfg = _read_json(self._retention_path())
        mode = cfg.get("mode") if cfg.get("mode") in ("off", "keep_last", "keep_days") else "off"
        return {"mode": mode, "keep_last": int(cfg.get("keep_last", 5) or 5), "keep_days": int(cfg.get("keep_days", 90) or 90),
                "updated_at": cfg.get("updated_at"), "auto_enabled": False,
                "auto_note": "BLOCKED — aguardando autorização: não existe execução automática. A política só alimenta a prévia e a limpeza MANUAL abaixo.",
                "options": [
                    {"mode": "off", "title": "Manter tudo (padrão)", "impact": "Nada é sugerido para exclusão. O espaço só cresce; você exclui na mão quando quiser."},
                    {"mode": "keep_last", "title": "Manter os N backups validados mais recentes por loja", "impact": "Os mais antigos viram candidatos à exclusão. O último validado nunca entra na lista."},
                    {"mode": "keep_days", "title": "Manter só os últimos N dias", "impact": "Backups validados mais velhos que N dias viram candidatos. O último validado de cada loja nunca entra na lista."}]}

    def retention_set(self, mode: object, keep_last: object = None, keep_days: object = None) -> dict:
        current = self.retention_get()
        if mode not in ("off", "keep_last", "keep_days"):
            raise BackupError("POLITICA_INVALIDA")
        def number(value: object, default: int, minimum: int, maximum: int) -> int:
            if value is None:
                return default
            if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
                raise BackupError("POLITICA_INVALIDA")
            return value
        last = number(keep_last, current["keep_last"], MIN_KEEP_LAST, 1000)
        days = number(keep_days, current["keep_days"], MIN_KEEP_DAYS, 3650)
        _write_json_private(self._retention_path(), {"mode": mode, "keep_last": last, "keep_days": days, "updated_at": self._now()})
        self._event("-", "retencao_configurada", f"Política de retenção definida: {mode}. Nenhuma exclusão automática foi ativada.")
        return self.retention_get()

    def _retention_candidates(self, policy: dict) -> list[dict]:
        out: list[dict] = []
        if policy["mode"] == "off":
            return out
        now = self._now()
        for slug in self._all_slugs():
            valid = [r for r in self._records(slug) if r["kind"] in KINDS_BACKUP and r["state"] == CONCLUIDO]      # já vem do mais novo para o mais antigo
            if len(valid) <= 1:
                continue                                                    # o último validado nunca é candidato
            for index, rec in enumerate(valid):
                if index == 0:
                    continue
                if policy["mode"] == "keep_last" and index >= policy["keep_last"]:
                    reason = f"fora dos {policy['keep_last']} mais recentes"
                elif policy["mode"] == "keep_days" and (now - (rec.get("finished_at") or rec["started_at"])) > policy["keep_days"] * 86400:
                    reason = f"mais antigo que {policy['keep_days']} dias"
                else:
                    continue
                out.append({"slug": slug, "id": rec["id"], "short_id": rec["id"][:8], "started_at": rec["started_at"], "size_bytes": rec.get("size_bytes") or 0,
                            "kind": rec["kind"], "reason": reason, "protection": (self.view(rec)["protection"] or {}).get("state")})
        return out

    def retention_preview(self) -> dict:
        policy = self.retention_get()
        candidates = self._retention_candidates(policy)
        code = secrets.token_urlsafe(18)
        with self._guard:
            self._confirm = {k: v for k, v in self._confirm.items() if v["exp"] > self._clock()}
            self._confirm[code] = {"kind": "retention", "ids": sorted(f"{c['slug']}/{c['id']}" for c in candidates), "exp": self._clock() + CONFIRM_TTL}
        return {"policy": policy, "candidates": candidates, "total_bytes": sum(c["size_bytes"] for c in candidates), "confirm_token": code,
                "confirm_text": "APAGAR", "expires_in": CONFIRM_TTL,
                "warning": "Estes arquivos serão APAGADOS deste celular e não há como desfazer. Nada é apagado até você confirmar. O último backup validado de cada loja nunca entra na lista."}

    def retention_apply(self, confirm_token: object, confirm_text: object) -> dict:
        with self._guard:
            info = self._confirm.get(confirm_token) if isinstance(confirm_token, str) else None
            if not info or info.get("kind") != "retention" or info["exp"] < self._clock() or confirm_text != "APAGAR":
                raise BackupError("CONFIRMACAO", "Digite APAGAR e use o código da prévia atual.")
            self._confirm.pop(confirm_token, None)
        policy = self.retention_get()
        allowed = set(info["ids"])
        deleted, skipped = [], []
        for cand in self._retention_candidates(policy):                      # recalcula agora: só apaga o que continua elegível E estava na prévia
            key = f"{cand['slug']}/{cand['id']}"
            if key not in allowed:
                continue
            with self._guard:
                busy = bool(self._active.get(cand["slug"]))
                if not busy:
                    self._active[cand["slug"]] = cand["id"]
            if busy:
                skipped.append({"slug": cand["slug"], "short_id": cand["short_id"], "why": "loja com operação em andamento"})
                continue
            try:
                self._delete_core(cand["slug"], cand["id"], "retencao-manual")
                deleted.append({"slug": cand["slug"], "short_id": cand["short_id"], "size_bytes": cand["size_bytes"]})
            except BackupError:
                skipped.append({"slug": cand["slug"], "short_id": cand["short_id"], "why": "falha ao apagar"})
            finally:
                self._release(cand["slug"], cand["id"])
        return {"deleted": deleted, "skipped": skipped, "freed_bytes": sum(d["size_bytes"] for d in deleted)}

    # ----- espaço
    def storage(self, slug: str | None = None) -> dict:
        free = self._free(slug or "-")
        def one(name: str) -> dict:
            folder = self._slug_dir(name)
            files = ([p for p in folder.glob("*.zip")] + [p for p in folder.glob(f"*{bcrypto.FILE_EXTENSION}")]) if folder.is_dir() else []
            files = [p for p in files if p.is_file() and ID_RE.fullmatch(p.stem)]
            return {"slug": name, "site_bytes": dir_size(self.sites / name), "catalog_bytes": dir_size(self.catalogs / name),
                    "backup_bytes": sum(p.stat().st_size for p in files), "backup_count": len(files),
                    "encrypted_count": sum(1 for p in files if p.suffix == bcrypto.FILE_EXTENSION), "legacy_count": sum(1 for p in files if p.suffix == ".zip")}
        if slug:
            data = one(self._slug(slug))
        else:
            rows = [one(n) for n in self._all_slugs()]
            data = {"sites_bytes": sum(r["site_bytes"] for r in rows), "catalogs_bytes": sum(r["catalog_bytes"] for r in rows),
                    "backup_bytes": sum(r["backup_bytes"] for r in rows), "backup_count": sum(r["backup_count"] for r in rows),
                    "encrypted_count": sum(r["encrypted_count"] for r in rows), "legacy_count": sum(r["legacy_count"] for r in rows)}
        data.update(free_bytes=free, policy=POLICY, site_quota_bytes=None,
                    site_quota_note="Sem cota imposta pelo CDBS. Existem limites de upload: site até 50 MB e fotos até 60 MB por loja.")
        return data

    def _all_slugs(self) -> list[str]:
        names: set[str] = set()
        for base in (self.sites, self.catalogs, self.root):
            if base.is_dir():
                for p in base.iterdir():
                    if p.is_dir() and SLUG_RE.fullmatch(p.name):
                        names.add(p.name)
        return sorted(n for n in names if self.known(n))

    # ----- lembrete mensal (calculado ao abrir o painel; não depende de tarefa agendada)
    def _state_path(self, slug: str) -> Path:
        return self._slug_dir(slug) / "state.json"

    def _touch_seen(self, slug: str) -> int:
        with self._rec_lock:
            state = _read_json(self._state_path(slug))
            if not state.get("first_seen"):
                state["first_seen"] = self._now()
                _write_json_private(self._state_path(slug), state)
            return int(state["first_seen"])

    def last_validated(self, slug: str) -> int | None:
        times = [r["finished_at"] for r in self._records(slug) if r["kind"] in KINDS_BACKUP and r["state"] == CONCLUIDO and r.get("finished_at")]
        return max(times) if times else None

    def reminder(self, slug: str) -> dict:
        slug = self._slug(slug)
        now = self._now()
        last = self.last_validated(slug)
        ref = last or self._touch_seen(slug)
        overdue = now - ref >= REMINDER_DAYS * 86400
        out = {"overdue": overdue, "last_validated_at": last, "reference_at": ref, "days_since": (now - ref) // 86400, "shown_count": 0, "last_shown_at": None,
               "message": "", "last_processed_at": None}
        with self._rec_lock:
            state = _read_json(self._state_path(slug))
            out["last_processed_at"] = state.get("processed_at")
            info = state.get("reminder", {})
            if overdue:
                if info.get("ref") != ref:
                    info = {"ref": ref, "count": 0, "last_at": 0}
                if now - int(info.get("last_at", 0)) >= REMINDER_LOG_SECONDS:      # evita alerta duplicado a cada abertura
                    info["count"] = int(info.get("count", 0)) + 1
                    info["last_at"] = now
                    state["reminder"] = info
                    _write_json_private(self._state_path(slug), state)
                    self._event(slug, "lembrete", f"Aviso de backup atrasado exibido ({(now - ref) // 86400} dias).")
                out["shown_count"], out["last_shown_at"] = int(info.get("count", 0)), info.get("last_at") or None
                out["message"] = (f"Esta loja está há {(now - ref) // 86400} dias sem backup validado." if last
                                  else f"Esta loja nunca teve backup validado ({(now - ref) // 86400} dias desde que o CDBS a conheceu).")
        return out

    def _status_path(self) -> Path:
        return self.root / "reminder-status.json"

    def reminder_tick(self, source: str = "painel") -> dict:
        """Processa o lembrete de TODAS as lojas (conta o prazo das que nunca foram abertas, registra o aviso no máximo 1×/dia e anota quando rodou).
        O CDBS chama isto ao iniciar e a cada 6 h enquanto estiver rodando. Se o Android encerrar o Termux, nada roda até iniciar de novo."""
        slugs = self._all_slugs()
        overdue = 0
        now = self._now()
        for slug in slugs:
            self._touch_seen(slug)
            if self.reminder(slug)["overdue"]:
                overdue += 1
            with self._rec_lock:
                state = _read_json(self._state_path(slug))
                state["processed_at"] = now
                _write_json_private(self._state_path(slug), state)
        status = {"last_run_at": now, "source": source, "stores": len(slugs), "overdue": overdue,
                  "note": "Verificado ao iniciar o CDBS e a cada 6 horas enquanto ele estiver rodando. Não envia notificação do Android e não roda com o celular desligado ou o Termux encerrado."}
        with self._rec_lock:
            _ensure_dir(self.root)
            _write_json_private(self._status_path(), status)
        return status

    def reminder_status(self) -> dict:
        data = _read_json(self._status_path())
        return data or {"last_run_at": None, "source": None, "stores": 0, "overdue": 0,
                        "note": "Ainda não foi verificado. Acontece ao iniciar o CDBS e a cada 6 horas enquanto ele estiver rodando."}

    # ----- visão geral
    def overview(self) -> dict:
        rows = []
        for slug in self._all_slugs():
            records = self._records(slug)
            latest = next((r for r in records if r["kind"] in KINDS_BACKUP and r["state"] != EXCLUIDO), None)
            storage = self.storage(slug)
            reminder = self.reminder(slug)
            store = _read_json(self.catalogs / slug / "store.json")
            rows.append({"slug": slug, "has_site": (self.sites / slug / "index.html").is_file(), "has_catalog": bool(store), "store_pending": bool(store.get("pending")),
                         "last_validated_at": reminder["last_validated_at"], "last_state": latest["state"] if latest else None,
                         "backup_bytes": storage["backup_bytes"], "backup_count": storage["backup_count"], "encrypted_count": storage["encrypted_count"],
                         "legacy_count": storage["legacy_count"], "site_bytes": storage["site_bytes"],
                         "catalog_bytes": storage["catalog_bytes"], "reminder": reminder,
                         "active": self._active_view(slug)})
        return {"stores": rows, "storage": self.storage(), "overdue": sum(1 for r in rows if r["reminder"]["overdue"]),
                "protection": self.protection_status(), "retention": self.retention_get(), "reminder_status": self.reminder_status()}

    # ----- roteador (o cdbs.py só repassa)
    def handle(self, method: str, path: str, body: dict) -> tuple[int, dict]:
        parts = path.strip("/").split("/")
        if parts[:2] != ["api", "backups"] or len(parts) < 3:
            raise BackupError("NAO_ENCONTRADO")
        rest = parts[2:]
        if rest == ["overview"] and method == "GET":
            return 200, self.overview()
        # rotas globais começam com "_" (nenhum nome de loja pode começar assim)
        if rest[0] == "_protecao":
            if rest == ["_protecao"] and method == "GET":
                return 200, {"protection": self.protection_status()}
            if rest == ["_protecao", "setup"] and method == "POST":
                return 201, {"protection": self.setup_protection(body.get("password"), body.get("confirm"))}
            raise BackupError("NAO_ENCONTRADO")
        if rest[0] == "_retencao":
            if rest == ["_retencao"] and method == "GET":
                return 200, {"policy": self.retention_get()}
            if rest == ["_retencao"] and method == "POST":
                return 200, {"policy": self.retention_set(body.get("mode"), body.get("keep_last"), body.get("keep_days"))}
            if rest == ["_retencao", "preview"] and method == "POST":
                return 200, {"preview": self.retention_preview()}
            if rest == ["_retencao", "apply"] and method == "POST":
                return 200, {"result": self.retention_apply(body.get("confirm_token"), body.get("confirm_text"))}
            raise BackupError("NAO_ENCONTRADO")
        slug, sub = rest[0], rest[1:]
        if not sub and method == "GET":
            return 200, self.list(slug)
        if sub == ["create"] and method == "POST":
            rec, created = self.start_backup(slug, body.get("password"))
            return (202 if created else 200), {"backup": rec, "deduplicated": not created}
        if len(sub) >= 1 and ID_RE.fullmatch(sub[0]):
            rid, tail = sub[0], sub[1:]
            if not tail and method == "GET":
                return 200, {"backup": self.get(slug, rid)}
            if tail == ["validate"] and method == "POST":
                return 200, {"backup": self.validate(slug, rid, body.get("password"))}
            if tail == ["protect"] and method == "POST":
                rec, _created = self.start_protect(slug, rid, body.get("password"))
                return 202, {"operation": rec}
            if tail == ["restore", "summary"] and method == "POST":
                return 200, {"summary": self.restore_summary(slug, rid)}
            if tail == ["restore"] and method == "POST":
                rec, _created = self.start_restore(slug, rid, body.get("confirm_token"), body.get("confirm_slug"), body.get("password"), body.get("confirm_recreate") is True)
                return 202, {"operation": rec}
            if tail == ["delete", "summary"] and method == "POST":
                return 200, {"summary": self.delete_summary(slug, rid)}
            if tail == ["delete"] and method == "POST":
                return 200, {"backup": self.delete_backup(slug, rid, body.get("confirm_token"), body.get("confirm_text"), body.get("confirm_last") is True)}
        raise BackupError("NAO_ENCONTRADO")
