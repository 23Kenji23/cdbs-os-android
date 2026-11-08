"""CDBS.OS — atualização pelo painel com cópia de segurança e volta automática (Parte 2.5).

Fluxo (quem executa os passos que reiniciam o servidor é o SUPERVISOR, outro processo, para a atualização não
depender do processo que ela mesma substitui):

  1. RECEBIDO    o painel envia o ZIP; ele fica em `data/updates/staging/<id>/` — nada da versão ativa muda.
  2. VALIDANDO   formato (arquivos na raiz ou dentro de UMA pasta), caminhos, links, segredos, dados, tamanho,
                 arquivos obrigatórios, manifesto VERSION.json com SHA-256 de cada arquivo, sintaxe do Python,
                 formato dos dados e versão (mais nova; igual/mais antiga só com confirmação extra).
  3. CÓPIA       cópia conferida (SHA-256) de TODOS os arquivos do programa atual em `data/updates/versions/`.
  4. PARANDO / INSTALANDO / INICIANDO / VERIFICANDO
                 o supervisor para o servidor, copia o programa novo arquivo por arquivo (troca atômica de cada
                 arquivo), inicia a versão nova e confere a saúde: a rota de saúde responde com a versão NOVA, o
                 servidor dos sites responde e o supervisor novo passa no autoteste.
  5. CONCLUÍDO, ou REVERTENDO → REVERTIDO (a versão anterior volta e é conferida), ou FALHOU_REVERSAO (a cópia
                 fica guardada e as instruções de recuperação manual são mostradas).

`.env` e `data/` NUNCA são tocados. Se o Android matar tudo no meio, o próximo `bash start.sh` lê o diário
(`data/updates/journal.json`) e termina a volta para a versão anterior antes de iniciar o servidor.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import stat
import sys
import tempfile
import time
import zipfile
from pathlib import Path, PurePosixPath
from typing import Callable

import versioninfo
from sysutil import FileLock, ensure_private_dir, fmt_local, read_json, scrub, write_json_private

MAX_PACKAGE = 25 * 1024 * 1024
MAX_UNPACKED = 80 * 1024 * 1024
MAX_FILES = 800
MAX_DEPTH = 12
KEEP_VERSIONS = 2
REQUIRED = ("cdbs.py", "catalog.py", "backup.py", "backup_crypto.py", "supervisor.py", "updater.py", "sysutil.py", "config.py",
            "versioninfo.py", "audit.py", "lifecycle.py", "doctor.py", "start.sh", "web/index.html", "web/app.js", "client/index.html",
            versioninfo.MANIFEST)
SECRET_NAMES = {".env", "admin.json", "secret.key", "protection.json", "secrets.json", "admin-sessions.json"}
SECRET_SUFFIXES = (".pem", ".key", ".p12", ".pfx")
ID_RE = re.compile(r"^[0-9a-f]{12}$")
SNAP_RE = re.compile(r"^[0-9A-Za-z._-]{1,80}$")
TERMINAL = {"CONCLUIDO", "REVERTIDO", "FALHOU", "FALHOU_REVERSAO"}
STEP_LABELS = {
    "VALIDANDO": "validando o pacote", "COPIA_DE_SEGURANCA": "fazendo a cópia de segurança do programa atual",
    "PARANDO": "parando o servidor", "INSTALANDO": "copiando os arquivos novos", "INICIANDO": "iniciando a versão nova",
    "VERIFICANDO": "conferindo a saúde da versão nova", "CONCLUIDO": "concluída", "REVERTENDO": "voltando para a versão anterior",
    "REVERTIDO": "revertida (versão anterior recolocada)", "FALHOU": "falhou (nada foi alterado)",
    "FALHOU_REVERSAO": "falhou e a volta automática também falhou (veja as instruções)",
}


class UpdateError(Exception):
    def __init__(self, message: str, status: int = 400, code: str = "PACOTE_INVALIDO"):
        super().__init__(message)
        self.status, self.code = status, code


def _safe_name(raw: str) -> PurePosixPath:
    name = raw.replace("\\", "/")
    if "\x00" in name:
        raise UpdateError("O pacote tem um nome de arquivo inválido.")
    path = PurePosixPath(name)
    if name.startswith("/") or (path.parts and ":" in path.parts[0]) or any(p in ("..", ".") for p in path.parts) or len(path.parts) > MAX_DEPTH:
        raise UpdateError("O pacote tem caminhos perigosos (../, absolutos ou fundos demais).")
    return path


def inspect_archive(archive: zipfile.ZipFile, current: dict) -> dict:
    """Confere tudo o que dá para conferir SEM extrair. Devolve o relatório (com `problems` vazios se aprovado)."""
    checks: list[dict] = []
    problems: list[str] = []

    def check(name: str, ok: bool, message: str) -> bool:
        checks.append({"name": name, "ok": ok, "message": message})
        if not ok:
            problems.append(message)
        return ok

    members, total, seen_lower = [], 0, set()
    for info in archive.infolist():
        path = _safe_name(info.filename)
        mode = (info.external_attr >> 16) & 0xFFFF
        if stat.S_ISLNK(mode):
            raise UpdateError("O pacote tem links simbólicos.")
        if not path.parts or info.filename.endswith("/"):
            continue
        if path.parts[0].lower() == "__macosx" or "__pycache__" in path.parts or path.suffix in (".pyc", ".pyo") or path.name == ".DS_Store":
            continue
        lower = path.as_posix().lower()
        if lower in seen_lower:
            raise UpdateError("O pacote tem arquivos repetidos (mesmo nome).")
        seen_lower.add(lower)
        members.append((info, path))
        total += info.file_size
    check("tamanho", len(members) <= MAX_FILES and total <= MAX_UNPACKED,
          f"{len(members)} arquivos, {total // 1024} KB descompactado (limite {MAX_FILES} arquivos / {MAX_UNPACKED // (1024 * 1024)} MB)")
    names = {p.as_posix() for _, p in members}
    if "cdbs.py" in names:
        prefix, layout = "", "arquivos na raiz do ZIP"
    else:
        tops = {p.parts[0] for _, p in members}
        if len(tops) == 1 and f"{next(iter(tops))}/cdbs.py" in names:
            prefix, layout = next(iter(tops)) + "/", f"arquivos dentro da pasta {next(iter(tops))}/"
        else:
            raise UpdateError("O pacote não tem cdbs.py na raiz nem dentro de uma única pasta. É o arquivo certo?")
    check("formato", True, layout)
    rels = {}
    for info, path in members:
        rel = path.as_posix()[len(prefix):]
        base = rel.rsplit("/", 1)[-1]
        if base in SECRET_NAMES or base.endswith(SECRET_SUFFIXES) or base.startswith("id_rsa") or base.startswith("id_ed25519"):
            raise UpdateError(f"O pacote traz um arquivo de segredo ({base}). Ele nunca pode vir num pacote.")
        if rel.startswith("data/") and rel != "data/sites/.keep":
            raise UpdateError("O pacote traz dados (data/...). Um pacote de atualização só pode ter o programa.")
        if rel.startswith(".git/") or "/.git/" in rel:
            raise UpdateError("O pacote traz uma pasta .git.")
        rels[rel] = info
    missing = [r for r in REQUIRED if r not in rels]
    check("arquivos obrigatórios", not missing, "todos presentes" if not missing else f"faltando: {', '.join(missing[:6])}")
    manifest: dict = {}
    if versioninfo.MANIFEST in rels:
        try:
            manifest = json.loads(archive.read(rels[versioninfo.MANIFEST]).decode("utf-8"))
        except (ValueError, UnicodeDecodeError, KeyError):
            manifest = {}
    version = str(manifest.get("version", "")) if isinstance(manifest, dict) else ""
    listed = manifest.get("files") if isinstance(manifest, dict) and isinstance(manifest.get("files"), dict) else {}
    if check("manifesto", bool(versioninfo.VERSION_RE.fullmatch(version)) and bool(listed),
             f"VERSION.json: versão {version}" if versioninfo.VERSION_RE.fullmatch(version) else "VERSION.json ausente ou inválido (pacotes anteriores à 3.0 só pelo atualizar.sh)"):
        program = {r for r in rels if r != versioninfo.MANIFEST}
        unlisted = sorted(program - set(listed))
        absent = sorted(set(listed) - program)
        check("lista de arquivos", not unlisted and not absent,
              "o pacote tem exatamente os arquivos do manifesto" if not unlisted and not absent else
              f"divergência com o manifesto: {', '.join((unlisted + absent)[:5])}")
        data_format = int(manifest.get("data_format", versioninfo.DATA_FORMAT) or versioninfo.DATA_FORMAT)
        check("formato dos dados", data_format == versioninfo.DATA_FORMAT,
              f"formato {data_format} (o instalado é {versioninfo.DATA_FORMAT})" + ("" if data_format == versioninfo.DATA_FORMAT else
              " — exige migração de dados, que esta atualização pelo painel não faz"))
    cur = current.get("version", "desconhecida")
    relation = "desconhecida"
    if versioninfo.VERSION_RE.fullmatch(version) and versioninfo.VERSION_RE.fullmatch(str(cur)):
        a, b = versioninfo.version_tuple(version), versioninfo.version_tuple(cur)
        relation = "mais nova" if a > b else "igual" if a == b else "mais antiga"
    return {"version": version, "current_version": cur, "relation": relation, "layout": layout, "prefix": prefix,
            "files": len(rels), "unpacked_bytes": total, "checks": checks, "problems": problems, "manifest": {"files": listed}}


class Updater:
    def __init__(self, root: Path, data: Path, *, clock: Callable[[], float] = time.time, disk_free: Callable[[Path], int] | None = None,
                 keep_versions: int = KEEP_VERSIONS):
        self.root, self.data = root, data
        self.folder = data / "updates"
        self.staging = self.folder / "staging"
        self.versions = self.folder / "versions"
        self.journal_path = self.folder / "journal.json"
        self._clock = clock
        self._disk_free = disk_free or (lambda p: shutil.disk_usage(p).free)
        self.keep_versions = keep_versions

    # ----- versão instalada e arquivos do programa
    def current(self) -> dict:
        return versioninfo.load(self.root)

    def current_program_files(self) -> list[str]:
        info = self.current()
        if info["valid"] and info["files"]:
            return sorted(set(info["files"]) | {versioninfo.MANIFEST})
        listing = versioninfo.build_manifest(self.root, "0.0.0", "")["files"]           # sem manifesto: tudo que não é dado
        return sorted(listing)

    # ----- recebimento e validação (lado do painel)
    def stage_upload(self, reader, length: int) -> dict:
        if not isinstance(length, int) or length <= 0:
            raise UpdateError("Envie o arquivo ZIP do pacote.")
        if length > MAX_PACKAGE:
            raise UpdateError(f"O pacote passa do limite de {MAX_PACKAGE // (1024 * 1024)} MB.", 413)
        if self._disk_free(self.data if self.data.exists() else self.root) < length * 4 + 20 * 1024 * 1024:
            raise UpdateError("Não há espaço livre suficiente para receber e conferir o pacote.", 507, "ESPACO_INSUFICIENTE")
        sid = secrets.token_hex(6)
        folder = ensure_private_dir(self.staging / sid)
        package = folder / "package.zip"
        received = 0
        try:
            with open(package, "wb") as handle:
                while received < length:
                    chunk = reader.read(min(256 * 1024, length - received))
                    if not chunk:
                        break
                    handle.write(chunk)
                    received += len(chunk)
            if received != length:
                raise UpdateError("O envio do pacote ficou incompleto. Tente de novo.")
            report = self._validate_and_extract(sid, package)
        except UpdateError:
            shutil.rmtree(folder, ignore_errors=True)
            raise
        except Exception:                                                           # noqa: BLE001
            shutil.rmtree(folder, ignore_errors=True)
            raise UpdateError("Não foi possível conferir o pacote.", 500, "ERRO_INTERNO") from None
        return report

    def stage_file(self, path: Path) -> dict:
        with open(path, "rb") as handle:
            return self.stage_upload(handle, path.stat().st_size)

    def _validate_and_extract(self, sid: str, package: Path) -> dict:
        try:
            archive = zipfile.ZipFile(package)
        except zipfile.BadZipFile:
            raise UpdateError("O arquivo não é um ZIP válido (ou está corrompido).") from None
        with archive:
            if archive.testzip() is not None:
                raise UpdateError("O ZIP está corrompido (falha na conferência interna).")
            report = inspect_archive(archive, self.current())
            target = self.staging / sid / "files"
            target.mkdir()
            written = 0
            for info in archive.infolist():
                if info.filename.endswith("/"):
                    continue
                path = _safe_name(info.filename)
                if path.parts[0].lower() == "__macosx" or "__pycache__" in path.parts or path.suffix in (".pyc", ".pyo") or path.name == ".DS_Store":
                    continue
                rel = path.as_posix()[len(report["prefix"]):]
                dest = target.joinpath(*PurePosixPath(rel).parts)
                if target.resolve() not in dest.resolve().parents:
                    raise UpdateError("O pacote tentou escrever fora da pasta de preparação.")
                dest.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, open(dest, "xb") as output:
                    while chunk := source.read(256 * 1024):
                        written += len(chunk)
                        if written > MAX_UNPACKED:
                            raise UpdateError("O pacote descompactado passa do limite.", 413)
                        output.write(chunk)
                mode = (info.external_attr >> 16) & 0o777
                os.chmod(dest, 0o755 if (rel.endswith(".sh") or mode & 0o111) else 0o644)
        listed = report["manifest"]["files"]
        bad_hash = []
        for rel, meta in listed.items():
            path = target / rel
            if not path.is_file():
                bad_hash.append(rel)
                continue
            sha, size = versioninfo.sha256_file(path)
            if not isinstance(meta, dict) or sha != meta.get("sha256") or size != meta.get("size"):
                bad_hash.append(rel)
        if listed:
            report["checks"].append({"name": "integridade (SHA-256)", "ok": not bad_hash,
                                     "message": f"{len(listed)} arquivos conferem" if not bad_hash else f"não conferem: {', '.join(bad_hash[:5])}"})
            if bad_hash:
                report["problems"].append("arquivos do pacote não conferem com o manifesto (corrompido ou alterado)")
        syntax_errors = []
        for py in sorted(target.rglob("*.py")):
            try:
                compile(py.read_text(encoding="utf-8"), str(py.relative_to(target)), "exec")
            except (SyntaxError, UnicodeDecodeError, ValueError) as exc:
                syntax_errors.append(f"{py.relative_to(target).as_posix()} ({type(exc).__name__})")
        report["checks"].append({"name": "sintaxe do Python", "ok": not syntax_errors,
                                 "message": "todos os .py compilam" if not syntax_errors else f"erro em: {', '.join(syntax_errors[:4])}"})
        if syntax_errors:
            report["problems"].append("o código novo tem erro de sintaxe")
        report.pop("manifest", None)
        report.pop("prefix", None)
        report.update(id=sid, received_at=int(self._clock()), ok=not report["problems"], package_bytes=package.stat().st_size)
        write_json_private(self.staging / sid / "report.json", report)
        return report

    def staged(self) -> list[dict]:
        if not self.staging.is_dir():
            return []
        out = []
        for folder in sorted(self.staging.iterdir()):
            report = read_json(folder / "report.json")
            if isinstance(report, dict) and report.get("id") == folder.name:
                out.append(report)
        return sorted(out, key=lambda r: r.get("received_at", 0), reverse=True)

    def get_staged(self, sid: str) -> dict:
        if not ID_RE.fullmatch(sid or ""):
            raise UpdateError("Pacote não encontrado.", 404, "NAO_ENCONTRADO")
        report = read_json(self.staging / sid / "report.json")
        if not isinstance(report, dict) or report.get("id") != sid:
            raise UpdateError("Pacote não encontrado.", 404, "NAO_ENCONTRADO")
        return report

    def discard(self, sid: str) -> None:
        self.get_staged(sid)
        journal = self.journal()
        if journal and journal.get("step") not in TERMINAL and journal.get("staged_id") == sid:
            raise UpdateError("Este pacote está sendo instalado agora.", 409, "EM_ANDAMENTO")
        shutil.rmtree(self.staging / sid, ignore_errors=True)

    # ----- cópias guardadas (versões anteriores)
    def snapshots(self) -> list[dict]:
        if not self.versions.is_dir():
            return []
        out = []
        for folder in self.versions.iterdir():
            info = read_json(folder / "snapshot.json")
            if isinstance(info, dict) and info.get("id") == folder.name and info.get("verified"):
                out.append({k: info[k] for k in ("id", "version", "created_at", "created_ns", "files", "bytes", "reason") if k in info})
        return sorted(out, key=lambda s: s.get("created_ns") or s.get("created_at", 0) * 10**9, reverse=True)   # ordem exata, mesmo no mesmo segundo

    # ----- diário
    def journal(self) -> dict | None:
        data = read_json(self.journal_path)
        return data if isinstance(data, dict) and data.get("id") else None

    def history(self) -> list[dict]:
        data = read_json(self.folder / "history.json")
        return data.get("items", []) if isinstance(data, dict) else []

    def _set_step(self, journal: dict, step: str, message: str = "", **fields: object) -> dict:
        journal["step"] = step
        journal.setdefault("steps", []).append({"step": step, "at": int(self._clock())})
        journal["steps"] = journal["steps"][-30:]
        if message:
            journal["message"] = scrub(message, 400)
        journal.update(fields)
        write_json_private(self.journal_path, journal)
        if step in TERMINAL:
            hist = self.history()
            hist.insert(0, {k: journal.get(k) for k in ("id", "kind", "from_version", "to_version", "step", "message", "started_at", "snapshot")}
                        | {"finished_at": int(self._clock())})
            write_json_private(self.folder / "history.json", {"items": hist[:30]})
        return journal

    # ----- lado do supervisor
    def lock(self) -> FileLock:
        return FileLock(self.folder / ".update.lock")

    def begin(self, *, staged_id: str | None = None, snapshot_id: str | None = None, allow_same_or_older: bool = False, by: str = "painel") -> dict:
        current = self.current()
        if staged_id:
            report = self.get_staged(staged_id)
            if not report.get("ok"):
                raise UpdateError("Este pacote não passou na validação e não pode ser instalado.", 409, "PACOTE_INVALIDO")
            if report["relation"] in ("igual", "mais antiga") and not allow_same_or_older:
                raise UpdateError(f"O pacote é da versão {report['version']} ({report['relation']} que a instalada, {current['version']}). "
                                  "Para instalar mesmo assim, confirme a opção de reinstalar/voltar versão.", 409, "CONFIRMAR_VERSAO")
            source, to_version, kind = self.staging / staged_id / "files", report["version"], "atualizacao"
            manifest = read_json(source / versioninfo.MANIFEST)
            target_files = sorted(set((manifest or {}).get("files", {})) | {versioninfo.MANIFEST})
        elif snapshot_id:
            if not SNAP_RE.fullmatch(snapshot_id or ""):
                raise UpdateError("Cópia não encontrada.", 404, "NAO_ENCONTRADO")
            info = read_json(self.versions / snapshot_id / "snapshot.json")
            if not isinstance(info, dict) or not info.get("verified"):
                raise UpdateError("Cópia não encontrada ou não conferida.", 404, "NAO_ENCONTRADO")
            source, to_version, kind = self.versions / snapshot_id / "files", info["version"], "volta_de_versao"
            target_files = sorted(info["file_list"])
        else:
            raise UpdateError("Pedido inválido.")
        current_files = self.current_program_files()
        need = sum((self.root / r).stat().st_size for r in current_files if (self.root / r).is_file()) * 2 + 20 * 1024 * 1024
        if self._disk_free(self.data) < need:
            raise UpdateError("Não há espaço livre suficiente para a cópia de segurança e a instalação.", 507, "ESPACO_INSUFICIENTE")
        journal = {"id": secrets.token_hex(6), "kind": kind, "staged_id": staged_id, "snapshot_id": snapshot_id, "by": scrub(by, 40),
                   "from_version": current["version"], "to_version": to_version, "source": str(source.relative_to(self.data)),
                   "target_files": target_files, "previous_files": current_files, "started_at": int(self._clock()), "steps": []}
        return self._set_step(journal, "VALIDANDO", "pacote conferido de novo antes de instalar")

    def verify_source(self, journal: dict) -> None:
        """Logo antes de instalar: os arquivos preparados ainda conferem (ninguém mexeu na pasta de preparação)?"""
        source = self.data / journal["source"]
        manifest = read_json(source / versioninfo.MANIFEST)
        listed = (manifest or {}).get("files", {}) if isinstance(manifest, dict) else {}
        if journal["kind"] == "volta_de_versao":
            listed = read_json(self.versions / journal["snapshot_id"] / "snapshot.json").get("files_meta", {})
        for rel, meta in listed.items():
            path = source / rel
            if not path.is_file() or versioninfo.sha256_file(path)[0] != meta.get("sha256"):
                raise UpdateError(f"O arquivo preparado {rel} não confere mais (foi alterado ou corrompido).", 409, "PACOTE_ALTERADO")

    def make_snapshot(self, journal: dict, reason: str = "antes de atualizar") -> dict:
        self._set_step(journal, "COPIA_DE_SEGURANCA")
        snap_id = f"{journal['from_version']}-{time.strftime('%Y%m%d-%H%M%S', time.localtime(self._clock()))}-{journal['id'][:4]}"
        snap_id = re.sub(r"[^0-9A-Za-z._-]", "_", snap_id)
        folder = self.versions / snap_id
        files_dir = ensure_private_dir(folder / "files")
        meta: dict[str, dict] = {}
        total = 0
        for rel in journal["previous_files"]:
            src = self.root / rel
            if not src.is_file() or not versioninfo.is_program_path(rel) and rel != versioninfo.MANIFEST:
                continue
            dst = files_dir / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            sha, size = versioninfo.sha256_file(src)
            meta[rel] = {"sha256": sha, "size": size, "mode": stat.S_IMODE(src.stat().st_mode)}
            total += size
        for rel, info in meta.items():                                          # a cópia só vale se conferir
            if versioninfo.sha256_file(files_dir / rel)[0] != info["sha256"]:
                shutil.rmtree(folder, ignore_errors=True)
                raise UpdateError("A cópia de segurança não conferiu (disco com problema?). Nada foi alterado.", 500, "COPIA_FALHOU")
        write_json_private(folder / "snapshot.json", {"id": snap_id, "version": journal["from_version"], "created_at": int(self._clock()),
                                                      "created_ns": time.time_ns(),
                                                      "files": len(meta), "bytes": total, "file_list": sorted(meta), "files_meta": meta,
                                                      "verified": True, "reason": reason})
        self._set_step(journal, "COPIA_DE_SEGURANCA", f"cópia conferida: {len(meta)} arquivos", snapshot=snap_id)
        return journal

    def _copy_tree(self, source: Path, files: list[str], modes: dict | None = None) -> None:
        for rel in files:
            src = source / rel
            if not src.is_file():
                continue
            dst = self.root / rel
            if not (versioninfo.is_program_path(rel) or rel == versioninfo.MANIFEST):
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=f".{dst.name}.", suffix=".cdbs-novo", dir=dst.parent)
            os.close(fd)
            shutil.copyfile(src, tmp)
            mode = (modes or {}).get(rel, {}).get("mode") if modes else None
            os.chmod(tmp, mode if isinstance(mode, int) else (0o755 if rel.endswith(".sh") or os.access(src, os.X_OK) else 0o644))
            os.replace(tmp, dst)

    def _remove_extra(self, keep: list[str], candidates: list[str]) -> None:
        keep_set = set(keep)
        for rel in candidates:
            if rel in keep_set or not versioninfo.is_program_path(rel) or rel in (".env",) or rel.startswith("data/"):
                continue
            (self.root / rel).unlink(missing_ok=True)

    def install(self, journal: dict, hook: Callable[[str], None] | None = None) -> dict:
        self._set_step(journal, "INSTALANDO")
        source = self.data / journal["source"]
        files = [r for r in journal["target_files"] if r != versioninfo.MANIFEST]
        modes = read_json(self.versions / journal["snapshot_id"] / "snapshot.json").get("files_meta") if journal["kind"] == "volta_de_versao" else None
        for index, rel in enumerate(files):
            self._copy_tree(source, [rel], modes)
            if hook is not None:
                hook(f"instalado:{index}")                                      # os testes interrompem aqui de propósito
        self._remove_extra(journal["target_files"], journal["previous_files"])
        self._copy_tree(source, [versioninfo.MANIFEST], modes)                  # o manifesto por último: só diz "nova" depois de tudo copiado
        return self._set_step(journal, "INICIANDO")

    def restore(self, journal: dict) -> dict:
        self._set_step(journal, "REVERTENDO")
        snap = journal.get("snapshot")
        info = read_json(self.versions / str(snap) / "snapshot.json") if snap else {}
        if not isinstance(info, dict) or not info.get("verified"):
            raise UpdateError("Não há cópia de segurança conferida para voltar.", 500, "SEM_COPIA")
        source = self.versions / snap / "files"
        self._copy_tree(source, [r for r in info["file_list"] if r != versioninfo.MANIFEST], info.get("files_meta"))
        self._remove_extra(info["file_list"], journal["target_files"])
        self._copy_tree(source, [versioninfo.MANIFEST], info.get("files_meta"))
        for rel, meta in info["files_meta"].items():                            # conferência da volta
            path = self.root / rel
            if not path.is_file() or versioninfo.sha256_file(path)[0] != meta["sha256"]:
                raise UpdateError(f"A volta não conferiu no arquivo {rel}.", 500, "VOLTA_NAO_CONFERIU")
        return journal

    def finish(self, journal: dict, step: str, message: str) -> dict:
        journal = self._set_step(journal, step, message, finished_at=int(self._clock()))
        if step == "CONCLUIDO" and journal.get("staged_id"):
            shutil.rmtree(self.staging / journal["staged_id"], ignore_errors=True)
        if step in ("CONCLUIDO", "REVERTIDO"):
            self.prune_snapshots(keep_ids={journal.get("snapshot")})
        return journal

    def prune_snapshots(self, keep_ids: set | None = None) -> list[str]:
        """Guarda as `keep_versions` cópias mais recentes (e a da operação atual); as mais antigas saem."""
        removed = []
        snaps = self.snapshots()
        keep = {s["id"] for s in snaps[: self.keep_versions]} | {k for k in (keep_ids or set()) if k}
        for snap in snaps:
            if snap["id"] not in keep:
                shutil.rmtree(self.versions / snap["id"], ignore_errors=True)
                removed.append(snap["id"])
        return removed

    def recover_interrupted(self) -> dict | None:
        """Chamado pelo supervisor ANTES de iniciar o servidor. Termina com segurança uma atualização interrompida."""
        journal = self.journal()
        if not journal or journal.get("step") in TERMINAL:
            return None
        step = journal.get("step")
        if step in ("VALIDANDO", "COPIA_DE_SEGURANCA", "PARANDO"):
            if step == "COPIA_DE_SEGURANCA" and journal.get("snapshot"):
                info = read_json(self.versions / journal["snapshot"] / "snapshot.json")
                if not (isinstance(info, dict) and info.get("verified")):
                    shutil.rmtree(self.versions / journal["snapshot"], ignore_errors=True)
            return self.finish(journal, "FALHOU", "a atualização foi interrompida antes de instalar; nada do programa mudou")
        try:
            self.restore(journal)
        except UpdateError as exc:
            return self.finish(journal, "FALHOU_REVERSAO", f"interrompida durante a instalação e a volta falhou: {exc}")
        return self.finish(journal, "REVERTIDO", "a atualização foi interrompida no meio (o aparelho desligou ou o Termux foi fechado); "
                                                 "a versão anterior foi recolocada")

    def status(self) -> dict:
        journal = self.journal()
        view = None
        if journal:
            view = {k: journal.get(k) for k in ("id", "kind", "from_version", "to_version", "step", "message", "started_at", "finished_at", "snapshot", "steps")}
            view["label"] = STEP_LABELS.get(journal.get("step"), journal.get("step"))
            view["active"] = journal.get("step") not in TERMINAL
        return {"current": {k: self.current()[k] for k in ("name", "version", "released", "valid")}, "journal": view,
                "staged": self.staged(), "snapshots": self.snapshots(), "history": self.history()[:10]}


def doctor_checks(ctx) -> list:
    out = []
    upd = Updater(ctx.root, ctx.data)
    journal = upd.journal()
    if journal and journal.get("step") not in TERMINAL:
        out.append(("atualizações", "atualização em andamento", "AVISO",
                    f"uma atualização para {journal.get('to_version')} parou em {journal.get('step')}",
                    "Ao iniciar com bash start.sh o supervisor termina a volta para a versão anterior automaticamente."))
    elif journal and journal.get("step") == "FALHOU_REVERSAO":
        out.append(("atualizações", "última atualização", "FALHA", journal.get("message", ""),
                    "Rode: python3 updater.py voltar  (com o CDBS parado) — ou bash atualizar.sh com o ZIP anterior."))
    elif journal:
        out.append(("atualizações", "última atualização", "OK" if journal.get("step") in ("CONCLUIDO",) else "INFO",
                    f"{journal.get('from_version')} → {journal.get('to_version')}: {STEP_LABELS.get(journal.get('step'), journal.get('step'))} "
                    f"({fmt_local(journal.get('finished_at') or journal.get('started_at'))})"))
    snaps = upd.snapshots()
    out.append(("atualizações", "versões guardadas", "INFO", f"{len(snaps)} cópia(s) do programa para voltar atrás"
                + (f" (mais recente: {snaps[0]['version']})" if snaps else "")))
    return out


def _cli(argv: list[str]) -> int:
    root = Path(__file__).resolve().parent
    upd = Updater(root, root / "data")
    if len(argv) >= 3 and argv[1] == "verificar":
        try:
            with zipfile.ZipFile(argv[2]) as archive:
                report = inspect_archive(archive, upd.current())
        except (UpdateError, zipfile.BadZipFile, OSError) as exc:
            print(f"Pacote recusado: {exc}")
            return 1
        report.pop("manifest", None)
        print(json.dumps(report, ensure_ascii=False, indent=1))
        return 0 if not report["problems"] else 1
    if len(argv) >= 2 and argv[1] == "voltar":
        import control                                                          # noqa: PLC0415
        if control.supervisor_alive(upd.data):
            print("O supervisor está rodando: use o painel (Sistema → Atualizações → Voltar) ou pare o CDBS primeiro (Ctrl+C).")
            return 1
        snaps = upd.snapshots()
        wanted = argv[2] if len(argv) >= 3 else (snaps[0]["id"] if snaps else "")
        if not wanted:
            print("Não há cópia guardada para voltar.")
            return 1
        with upd.lock():
            journal = upd.begin(snapshot_id=wanted, by="linha de comando")
            upd.make_snapshot(journal, reason="antes de voltar versão (linha de comando)")
            upd.install(journal)
            upd.finish(journal, "CONCLUIDO", f"programa voltou para {journal['to_version']} pela linha de comando (sem verificação de saúde: inicie com bash start.sh)")
        print(f"Pronto: programa voltou para a versão {journal['to_version']}. Inicie com: bash start.sh")
        return 0
    print("Uso: python3 updater.py verificar <pacote.zip> | voltar [id-da-copia]")
    return 2


if __name__ == "__main__":
    sys.exit(_cli(sys.argv))
