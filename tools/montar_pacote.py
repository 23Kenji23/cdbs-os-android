#!/usr/bin/env python3
"""Monta o pacote de distribuição do CDBS.OS: gera VERSION.json (SHA-256 de cada arquivo do programa) e o ZIP.

Uso (na pasta do CDBS):
    python3 tools/montar_pacote.py 3.0.0 saida/CDBS-Android-Cloudflare.zip [--data AAAA-MM-DD]

Nunca inclui .env, data/ (exceto data/sites/.keep), __pycache__ nem *.pyc. Recusa montar se encontrar algo
com cara de segredo no meio dos arquivos do programa.
"""
from __future__ import annotations

import json
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import versioninfo  # noqa: E402

SECRET_NAMES = {".env", "admin.json", "secret.key", "protection.json", "secrets.json", "admin-sessions.json"}


def main(argv: list[str]) -> int:
    if len(argv) < 3 or not versioninfo.VERSION_RE.fullmatch(argv[1]):
        print(__doc__)
        return 2
    version, out = argv[1], Path(argv[2])
    released = argv[argv.index("--data") + 1] if "--data" in argv else time.strftime("%Y-%m-%d")
    manifest = versioninfo.build_manifest(ROOT, version, released)
    for rel in manifest["files"]:
        if rel.rsplit("/", 1)[-1] in SECRET_NAMES and rel != "data/sites/.keep":
            print(f"recusado: {rel} parece segredo", file=sys.stderr)
            return 1
    (ROOT / versioninfo.MANIFEST).write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    out.parent.mkdir(parents=True, exist_ok=True)
    names = sorted(manifest["files"]) + [versioninfo.MANIFEST]
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for rel in names:
            info = zipfile.ZipInfo.from_file(ROOT / rel, rel)
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, (ROOT / rel).read_bytes())
    print(f"{len(names)} arquivos · versão {version} · {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
