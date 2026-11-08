"""CDBS.OS — versão instalada e manifesto do pacote (`VERSION.json`).

O manifesto diz o nome, a versão, o formato dos dados e o SHA-256 de CADA arquivo do programa. Ele serve para:
  • o painel e o diagnóstico mostrarem a versão real;
  • o diagnóstico detectar arquivo do programa faltando ou alterado;
  • o atualizador conferir um pacote antes de instalar e saber exatamente quais arquivos são "programa"
    (todo o resto — data/, .env — é dado do usuário e nunca é tocado).
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path, PurePosixPath

from sysutil import read_json

MANIFEST = "VERSION.json"
DATA_FORMAT = 1                          # formato dos dados em data/; mudar exige migração explícita
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
# O que NUNCA é programa (e por isso nunca vai num pacote nem é trocado numa atualização).
NEVER_PROGRAM_PREFIXES = ("data/",)
NEVER_PROGRAM_NAMES = {".env", MANIFEST}


def is_program_path(rel: str) -> bool:
    p = PurePosixPath(rel)
    if rel in NEVER_PROGRAM_NAMES or any(rel.startswith(x) for x in NEVER_PROGRAM_PREFIXES):
        return rel == "data/sites/.keep"
    if "__pycache__" in p.parts or rel.endswith((".pyc", ".pyo")):
        return False
    return True


def sha256_file(path: Path) -> tuple[str, int]:
    digest, size = hashlib.sha256(), 0
    with path.open("rb") as handle:
        while chunk := handle.read(256 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def version_tuple(text: object) -> tuple[int, int, int]:
    if not isinstance(text, str) or not VERSION_RE.fullmatch(text):
        return (0, 0, 0)
    a, b, c = (int(x) for x in text.split("."))
    return (a, b, c)


def load(root: Path) -> dict:
    data = read_json(root / MANIFEST)
    if not isinstance(data, dict) or not VERSION_RE.fullmatch(str(data.get("version", ""))):
        return {"name": "CDBS.OS", "version": "desconhecida", "released": "", "data_format": DATA_FORMAT, "files": {}, "valid": False}
    files = data.get("files") if isinstance(data.get("files"), dict) else {}
    return {"name": str(data.get("name", "CDBS.OS"))[:40], "version": data["version"], "released": str(data.get("released", ""))[:20],
            "data_format": int(data.get("data_format", DATA_FORMAT) or DATA_FORMAT), "files": files, "valid": True}


def verify(root: Path, info: dict | None = None) -> dict:
    """Confere os arquivos do programa contra o manifesto: faltando, alterados. Não lê data/."""
    info = info or load(root)
    missing, changed = [], []
    for rel, meta in sorted((info.get("files") or {}).items()):
        path = root / rel
        if not path.is_file():
            missing.append(rel)
            continue
        try:
            sha, size = sha256_file(path)
        except OSError:
            missing.append(rel)
            continue
        if not isinstance(meta, dict) or sha != meta.get("sha256") or size != meta.get("size"):
            changed.append(rel)
    return {"checked": len(info.get("files") or {}), "missing": missing, "changed": changed,
            "ok": bool(info.get("valid")) and not missing and not changed}


def build_manifest(root: Path, version: str, released: str, *, name: str = "CDBS.OS") -> dict:
    """Usado pela ferramenta de empacotamento: lista e assina (SHA-256) todos os arquivos do programa."""
    files: dict[str, dict] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        rel = path.relative_to(root).as_posix()
        if not is_program_path(rel):
            continue
        sha, size = sha256_file(path)
        files[rel] = {"sha256": sha, "size": size}
    return {"name": name, "version": version, "released": released, "data_format": DATA_FORMAT, "files": files}
