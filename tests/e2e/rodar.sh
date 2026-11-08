#!/usr/bin/env bash
# Testes de NAVEGADOR (ponta a ponta) do CDBS.OS — para rodar num COMPUTADOR com Linux, não no celular.
#
# Precisa: Python 3.10+, Node 18+ e o Playwright com o Chromium:
#     npm install playwright && npx playwright install chromium
#   (se o Playwright estiver instalado em outra pasta: NODE_PATH=/caminho/node_modules bash tests/e2e/rodar.sh ...)
#
# Uso, de dentro da pasta do CDBS:
#   bash tests/e2e/rodar.sh ops                       painel 3.0 pelo guardião (bash start.sh): posições, pausa, sites removidos,
#                                                     histórico, notificações, webhook, bot !Link, túnel, reinício, atualizar e voltar
#   bash tests/e2e/rodar.sh backups                   Central de backups (2.1): proteção, criptografia, restauração, exclusão...
#   bash tests/e2e/rodar.sh adega SITE.zip [FOTO.jpg] site da adega + botão escondido + painel do cliente (precisa do ZIP
#                                                     adega-corujao-site-portatil-2.zip e de uma foto grande, > 3 MB)
#
# Tudo roda numa CÓPIA desta pasta, numa pasta temporária, nas portas de teste 28088/28089/28090/28091, com o
# cloudflared, a API da Meta e um servidor de e-mail DE MENTIRA. Nada sai para a internet e a pasta original (com os seus dados) não é tocada.
# Saída: "TUDO OK" e código 0, ou a primeira falha e código diferente de 0. Fotos de tela ficam em <temporária>/e2e/shots.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
SRC="$(cd "$HERE/../.." && pwd)"
WHAT="${1:-}"
case "$WHAT" in ops|backups|adega) ;; *) sed -n 2,20p "$0"; exit 2 ;; esac
command -v node >/dev/null 2>&1 || { echo "Node não encontrado (precisa do Node 18+ com o Playwright)." >&2; exit 2; }
W="$(mktemp -d "${TMPDIR:-/tmp}/cdbs-e2e-XXXXXX")"
APP="$W/app"
mkdir -p "$W/e2e" && cp "$HERE"/*.js "$HERE"/fake_meta.py "$HERE"/fake_smtp.py "$W/e2e/"
echo "pasta do teste: $W"

copy_app() {       # cópia limpa: sem dados, sem .env, sem cache
  mkdir -p "$APP" && (cd "$SRC" && tar --exclude=./data --exclude=./.env --exclude='*/__pycache__' --exclude='*.pyc' -cf - .) | (cd "$APP" && tar -xf -)
  mkdir -p "$APP/data/sites" && : > "$APP/data/sites/.keep"
}
wait_port() { for _ in $(seq 1 75); do python3 -I -c "import socket, sys; socket.create_connection(('127.0.0.1', int(sys.argv[1])), 1)" "$1" 2>/dev/null && return 0; sleep 0.2; done; return 1; }
site_zip() {
  python3 -I - "$1" <<'PY'
import sys, zipfile
with zipfile.ZipFile(sys.argv[1], "w") as z:
    z.writestr("index.html", "<!doctype html><meta charset=utf-8><title>Loja teste</title><link rel=stylesheet href=style.css><h1>Loja teste</h1>")
    z.writestr("style.css", "h1{color:#2e70d1}")
PY
}
stop() {           # para o que este teste iniciou (pelo número do processo; nada de "matar por nome")
  if [ -n "${CDBS:-}" ]; then
    kill -TERM "$CDBS" 2>/dev/null
    for _ in $(seq 1 50); do kill -0 "$CDBS" 2>/dev/null || break; sleep 0.2; done
  fi
  [ -n "${META:-}" ] && kill "$META" 2>/dev/null
  [ -n "${SMTP:-}" ] && kill "$SMTP" 2>/dev/null
  wait 2>/dev/null
  CDBS=""; META=""; SMTP=""
}
trap stop EXIT

copy_app
if [ "$WHAT" = ops ]; then
  TUNNEL_URL="https://e2e-cdbs-teste.trycloudflare.com"
  if [ ! -f "$APP/VERSION.json" ]; then (cd "$APP" && python3 tools/montar_pacote.py 3.0.0 "$W/base.zip" >/dev/null) || exit 9; fi
  BASEVER=$(python3 -I -c "import json, sys; print(json.load(open(sys.argv[1]))['version'])" "$APP/VERSION.json")
  NEXTVER=$(python3 -I -c "import sys; a = sys.argv[1].split('.'); a[-1] = str(int(a[-1]) + 1); print('.'.join(a))" "$BASEVER")
  cp -r "$APP" "$W/build1" && (cd "$W/build1" && python3 tools/montar_pacote.py "$NEXTVER" "$W/next.zip" >/dev/null) || exit 9
  site_zip "$W/loja-teste.zip"
  python3 -I -c "import sys, zipfile; zipfile.ZipFile(sys.argv[1], 'w').writestr('oi.txt', 'isto não é o CDBS')" "$W/invalido.zip"
  python3 -I -c "import os, sys; open(sys.argv[1], 'wb').write(os.urandom(4096))" "$W/lixo.zip"
  cp "$APP/tests/fake_cloudflared.py" "$W/fake_cloudflared.py"
  printf '#!/bin/sh\nexec python3 -I %s "$@"\n' "$W/fake_cloudflared.py" > "$W/cloudflared" && chmod +x "$W/cloudflared"
  printf 'CDBS_SITE_PORT=28088\nCDBS_ADMIN_PORT=28089\nCDBS_PUBLIC_BASE=\nCDBS_WHATSAPP_API_BASE=http://127.0.0.1:28090\nCDBS_TUNNEL_METRICS_PORT=28091\nCDBS_SMTP_HOST=127.0.0.1\nCDBS_SMTP_PORT=28092\nCDBS_SMTP_SECURITY=none\n' > "$APP/.env"
  python3 -I "$W/e2e/fake_meta.py" 28090 "$W/meta.jsonl" "EAAGfalsoE2Etoken0123456789abcdefABCDEF" > "$W/meta.log" 2>&1 &
  META=$!
  python3 -I "$W/e2e/fake_smtp.py" 28092 "$W/smtp.jsonl" "dono.cdbs@gmail.com" "abcd efgh ijkl mnop" > "$W/smtp.log" 2>&1 &
  SMTP=$!
  (cd "$APP" && CDBS_CLOUDFLARED_BIN="$W/cloudflared" FAKE_CF_URL="$TUNNEL_URL" exec bash start.sh) > "$W/cdbs.log" 2>&1 &
  CDBS=$!
  wait_port 28089 || { echo "o CDBS não iniciou:"; cat "$W/cdbs.log"; exit 3; }
  timeout 900 node "$W/e2e/ops.js" "$W/loja-teste.zip" "$APP" "$W" "$BASEVER" "$NEXTVER" "$TUNNEL_URL"
  RESULT=$?
else
  printf 'CDBS_SITE_PORT=28088\nCDBS_ADMIN_PORT=28089\nCDBS_PUBLIC_BASE=\n' > "$APP/.env"
  (cd "$APP" && exec python3 -u cdbs.py) > "$W/cdbs.log" 2>&1 &
  CDBS=$!
  wait_port 28088 || { echo "o CDBS não iniciou:"; cat "$W/cdbs.log"; exit 3; }
  if [ "$WHAT" = backups ]; then
    site_zip "$W/loja-teste.zip"
    timeout 600 node "$W/e2e/backups.js" "$W/loja-teste.zip" "$APP"
    RESULT=$?
  else
    SITE="${2:-}"; PHOTO="${3:-}"
    [ -f "$SITE" ] || { echo "Informe o ZIP do site da adega: bash tests/e2e/rodar.sh adega adega-corujao-site-portatil-2.zip [foto.jpg]" >&2; exit 2; }
    if [ -n "$PHOTO" ]; then cp "$PHOTO" "$W/e2e/foto-grande.jpg"
    else python3 -c "from PIL import Image; import random, sys; im = Image.effect_noise((3200, 2400), 64).convert('RGB'); im.save(sys.argv[1], quality=95)" "$W/e2e/foto-grande.jpg" 2>/dev/null \
      || { echo "Sem foto: passe uma foto grande (> 3 MB) como 3º argumento (ou instale o Pillow)." >&2; exit 2; }
    fi
    timeout 300 node "$W/e2e/adega.js" "$SITE" "$APP"
    RESULT=$?
  fi
fi
stop
trap - EXIT
LEFT=$(pgrep -af "$W/" | grep -v pgrep || true)
if [ -n "$LEFT" ]; then echo "PROCESSOS QUE FICARAM:"; echo "$LEFT"; [ "$RESULT" = 0 ] && RESULT=4; fi
echo "resultado=$RESULT (registro do CDBS: $W/cdbs.log)"
exit "$RESULT"
