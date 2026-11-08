#!/usr/bin/env bash
# CDBS.OS — atualização reversível pelo Termux (caminho manual; o painel também atualiza, em Sistema → Atualizações).
#
# Uso:
#   bash atualizar.sh ~/storage/downloads/CDBS-Android-Cloudflare.zip     atualiza
#   bash atualizar.sh --voltar ~/cdbs-os-copia-AAAAMMDD-HHMMSS             volta para a cópia feita antes
#
# O que ele faz, nesta ordem (para em qualquer erro, sem deixar a pasta pela metade):
#   1. confere se o CDBS está parado (servidor E supervisor) — não atualiza com ele rodando;
#   2. confere o ZIP: aceita os arquivos na raiz OU dentro de UMA pasta (ex.: cdbs_package/) que tenha cdbs.py;
#      recusa caminhos perigosos, links, .env, segredos e dados (data/ só pode ter data/sites/.keep);
#      se o pacote tiver VERSION.json, confere o SHA-256 de cada arquivo;
#   3. extrai numa pasta temporária e confere a sintaxe do Python (antes de gastar espaço com a cópia);
#   4. confere o espaço livre e faz uma CÓPIA COMPLETA da pasta atual (programa + sites + catálogos + backups + senha);
#      só então copia os arquivos do programa;
#   5. NUNCA toca em .env nem em data/;
#   6. diz como voltar atrás.
#
# Todo o script fica dentro de main() e só é chamado na ÚLTIMA linha: assim o bash lê o arquivo inteiro antes de
# executar. No passo 5 este próprio atualizar.sh é trocado pela versão nova; sem isso, o bash continuaria lendo o
# arquivo novo no meio do caminho e pararia com um erro ("syntax error", "command not found") depois de já ter copiado
# tudo — é o que acontece com o atualizar.sh da 2.1 ao instalar a 3.0.
main() {
set -euo pipefail

DEST="$(cd "$(dirname "$0")" && pwd)"
STAMP="$(date +%Y%m%d-%H%M%S)"

fail() { echo "ERRO: $*" >&2; exit 1; }

port_of() {          # lê a porta do .env (se houver) sem executar o arquivo
  local key="$1" def="$2" val=""
  if [ -f "$DEST/.env" ]; then
    val="$(grep -E "^[[:space:]]*${key}[[:space:]]*=" "$DEST/.env" | tail -n1 | cut -d= -f2- | tr -d " \"'" || true)"
  fi
  echo "${val:-$def}"
}

server_running() {   # porta aberta OU supervisor vivo (ele pode estar esperando para reiniciar o servidor)
  python3 - "$DEST" "$(port_of CDBS_ADMIN_PORT 8089)" "$(port_of CDBS_SITE_PORT 8088)" <<'PY'
import json, os, socket, sys
root = sys.argv[1]
for port in sys.argv[2:]:
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=1):
            raise SystemExit(0)
    except (OSError, ValueError):
        pass
try:
    with open(os.path.join(root, "data", "system", "supervisor.json"), encoding="utf-8") as fh:
        info = json.load(fh)
    pid = int(info.get("pid") or 0)
    if pid and not info.get("stopped_at"):
        with open(f"/proc/{pid}/cmdline", "rb") as fh:
            if b"supervisor.py" in fh.read():
                raise SystemExit(0)
except (OSError, ValueError, TypeError, AttributeError):
    pass
raise SystemExit(1)
PY
}

# ------------------------------------------------------------------ voltar atrás
if [ "${1:-}" = "--voltar" ]; then
  COPY="${2:-}"
  [ -n "$COPY" ] || fail "diga qual cópia usar. Ex.: bash atualizar.sh --voltar ~/cdbs-os-copia-20261023-101500"
  COPY="$(cd "$COPY" 2>/dev/null && pwd)" || fail "a pasta da cópia não existe."
  [ -f "$COPY/cdbs.py" ] || fail "essa pasta não parece uma cópia do CDBS (falta cdbs.py)."
  [ "$COPY" != "$DEST" ] || fail "a cópia e a pasta atual são a mesma."
  if server_running; then fail "o CDBS ainda está rodando. Pare com Ctrl+C na sessão dele e rode de novo."; fi
  ASIDE="${DEST}-desfeita-${STAMP}"
  mv "$DEST" "$ASIDE"
  cp -a "$COPY" "$DEST"
  echo "Pronto: $DEST voltou a ser igual à cópia $COPY."
  echo "A versão que você desfez foi guardada (não apagada) em: $ASIDE"
  echo "Inicie de novo com: cd \"$DEST\" && bash start.sh"
  exit 0
fi

# ------------------------------------------------------------------ atualizar
ZIP="${1:-}"
[ -n "$ZIP" ] || fail "diga onde está o ZIP. Ex.: bash atualizar.sh ~/storage/downloads/CDBS-Android-Cloudflare.zip"
[ -f "$ZIP" ] || fail "não encontrei o arquivo: $ZIP"
command -v unzip >/dev/null 2>&1 || fail "falta o unzip. Rode: pkg install unzip -y"
command -v python3 >/dev/null 2>&1 || fail "falta o Python. Rode: pkg install python -y"

if server_running; then
  fail "o CDBS está rodando. Pare com Ctrl+C na sessão dele (e o túnel também) e rode de novo."
fi

echo "1/5 Conferindo o ZIP…"
PREFIX="$(python3 - "$ZIP" <<'PY'
import stat, sys, zipfile
from pathlib import PurePosixPath
SECRET = (".env", "admin.json", "secret.key", "protection.json", "secrets.json", "admin-sessions.json")
def die(msg):
    print(msg, file=sys.stderr); raise SystemExit(1)
try:
    zf = zipfile.ZipFile(sys.argv[1])
    names = []
    for info in zf.infolist():
        raw = info.filename.replace("\\", "/")
        if "\x00" in raw:
            die("o ZIP tem um nome de arquivo inválido.")
        p = PurePosixPath(raw)
        if raw.startswith("/") or (p.parts and ":" in p.parts[0]) or any(x in ("..", ".") for x in p.parts):
            die("o ZIP tem caminhos perigosos (../ ou absolutos). Não vou usar este arquivo.")
        if stat.S_ISLNK((info.external_attr >> 16) & 0xFFFF):
            die("o ZIP tem links simbólicos. Não vou usar este arquivo.")
        if p.parts and p.parts[0].lower() == "__macosx":
            continue
        if not raw.endswith("/"):
            names.append(raw)
except zipfile.BadZipFile:
    die("o arquivo não é um ZIP válido (ou está corrompido).")
if len(set(n.lower() for n in names)) != len(names):
    die("o ZIP tem arquivos repetidos.")
if "cdbs.py" in names:
    prefix = ""
else:
    tops = {n.split("/", 1)[0] for n in names}
    if len(tops) == 1 and f"{next(iter(tops))}/cdbs.py" in names:
        prefix = next(iter(tops)) + "/"
    else:
        die("o ZIP não tem cdbs.py na pasta principal nem dentro de uma única pasta. É o arquivo certo?")
for n in names:
    rel = n[len(prefix):]
    base = rel.rsplit("/", 1)[-1]
    if base in SECRET or base.endswith((".pem", ".key")) or base.startswith("id_rsa"):
        die(f"o ZIP traz um arquivo de segredo ({base}). Não vou usar este arquivo.")
    if rel.startswith("data/") and rel != "data/sites/.keep":
        die("o ZIP traz dados (data/...). Um pacote de atualização só pode ter o programa.")
print(prefix)
PY
)" || fail "pacote recusado (veja o motivo acima). Nada foi alterado."
[ -n "$PREFIX" ] && echo "    (arquivos dentro da pasta ${PREFIX%/} do ZIP)"

TMP="$(mktemp -d "${TMPDIR:-/tmp}/cdbs-atualizar.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT
echo "2/5 Extraindo numa pasta temporária…"
unzip -q "$ZIP" -d "$TMP/novo" || fail "não consegui extrair o ZIP. Nada foi alterado."
NEW="$TMP/novo/${PREFIX}"

echo "3/5 Conferindo o código novo (sintaxe e SHA-256)…"
python3 - "$NEW" "$DEST" <<'PY' || fail "o pacote não passou na conferência (veja acima). Nada foi alterado."
import hashlib, json, pathlib, sys
new, dest = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
for path in new.rglob("*.py"):
    compile(path.read_text(encoding="utf-8"), str(path), "exec")
manifest = new / "VERSION.json"
if manifest.is_file():
    data = json.loads(manifest.read_text(encoding="utf-8"))
    for rel, meta in data.get("files", {}).items():
        f = new / rel
        if not f.is_file() or hashlib.sha256(f.read_bytes()).hexdigest() != meta.get("sha256"):
            print(f"arquivo do pacote não confere com o VERSION.json: {rel}", file=sys.stderr)
            raise SystemExit(1)
    old = dest / "VERSION.json"
    print(f"    versão nova: {data.get('version', '?')}" + (f" (instalada: {json.loads(old.read_text()).get('version', '?')})" if old.is_file() else ""))
else:
    print("    aviso: pacote sem VERSION.json (versão anterior à 3.0): sem conferência de SHA-256.")
PY

NEED_KB="$(du -sk "$DEST" | cut -f1)"
FREE_KB="$(df -Pk "$(dirname "$DEST")" | awk 'NR==2 {print $4}')"
if [ -n "$FREE_KB" ] && [ "$FREE_KB" -lt $((NEED_KB + 51200)) ]; then
  fail "falta espaço para a cópia de segurança: precisa de ~$(( (NEED_KB + 51200) / 1024 )) MB, há $(( FREE_KB / 1024 )) MB livres. Nada foi alterado."
fi

COPY="${DEST}-copia-${STAMP}"
echo "4/5 Copiando a pasta atual inteira para $COPY (sites, catálogos, backups e senha inclusos)…"
cp -a "$DEST" "$COPY" || fail "não consegui fazer a cópia (falta espaço?). Nada foi alterado."

echo "5/5 Copiando os arquivos do programa (sem tocar em .env nem em data/)…"
python3 - "$NEW" "$DEST" <<'PY'
# Remove só arquivos de PROGRAMA da versão antiga que não existem na nova (listados no VERSION.json antigo).
import json, pathlib, sys
new, dest = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
old_manifest, new_manifest = dest / "VERSION.json", new / "VERSION.json"
if old_manifest.is_file() and new_manifest.is_file():
    old_files = set(json.loads(old_manifest.read_text(encoding="utf-8")).get("files", {}))
    new_files = set(json.loads(new_manifest.read_text(encoding="utf-8")).get("files", {}))
    for rel in sorted(old_files - new_files):
        if rel.startswith("data/") or rel in (".env", "VERSION.json") or ".." in rel:
            continue
        target = dest / rel
        if target.is_file():
            target.unlink()
            print(f"    removido (não existe mais na versão nova): {rel}")
PY
rm -rf "$NEW/data" "$NEW/.env"
cp -a "$NEW/." "$DEST/"
mkdir -p "$DEST/data/sites"
chmod +x "$DEST"/*.sh 2>/dev/null || true

echo
echo "Atualizado. Seus dados não foram tocados."
if python3 -c "import cryptography" >/dev/null 2>&1; then
  echo "Criptografia dos backups: disponível."
else
  echo "ATENÇÃO: falta a biblioteca de criptografia. Sem ela o CDBS não cria backups novos."
  echo "         Instale com: pkg install python-cryptography -y"
fi
echo
echo "Para iniciar:      cd \"$DEST\" && bash start.sh"
echo "Para conferir:     bash \"$DEST/diagnostico.sh\""
echo "Para VOLTAR ATRÁS: bash \"$DEST/atualizar.sh\" --voltar \"$COPY\""
echo "(a cópia $COPY ocupa espaço; apague-a você mesmo só quando tiver certeza de que está tudo bem)"
}
main "$@"; exit $?
