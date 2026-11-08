#!/usr/bin/env bash
# Uso: bash start-cloudflare.sh
# Abre UM túnel, só para a porta dos sites (8088). O painel dos clientes mora dentro dela,
# em /_painel/, então não precisa de túnel separado. O seu painel administrativo (8089) NUNCA vai para o túnel.
set -euo pipefail
cd "$(dirname "$0")"

case "${1:-sites}" in
  sites) ;;
  painel)
    echo "Não precisa mais de um túnel para o painel dos clientes."
    echo "Ele agora fica dentro do endereço dos sites: https://SEU-ENDERECO/_painel/?loja=NOME"
    echo "ou pelo botão escondido do próprio site (5 toques no logo)."
    echo "Rode só: bash start-cloudflare.sh"
    exit 0 ;;
  *) echo "Uso: bash start-cloudflare.sh" >&2; exit 2 ;;
esac

# Com o CDBS iniciado pelo bash start.sh (supervisor), o próprio supervisor liga o túnel: assim ele sabe o link,
# avisa quando cair, religa sozinho e o painel/bot conseguem mostrar o endereço.
if [ -f supervisor.py ] && python3 supervisor.py status >/dev/null 2>&1; then
  echo "O CDBS está rodando com o supervisor: pedindo a ele para ligar o túnel (só a porta dos sites)."
  exec python3 supervisor.py tunnel start
fi

if ! command -v cloudflared >/dev/null 2>&1; then
  echo "cloudflared não está instalado. No Termux, rode: pkg update -y && pkg install cloudflared -y" >&2
  exit 127
fi

PORT="${CDBS_SITE_PORT:-}"
if [ -z "$PORT" ] && [ -f .env ]; then
  PORT="$(python3 - <<'PY'
from pathlib import Path
port = ""
for raw in Path(".env").read_text(encoding="utf-8").splitlines():
    line = raw.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    if key.strip() == "CDBS_SITE_PORT":
        port = value.strip().strip("\"'")
print(port)
PY
)"
fi
PORT="${PORT:-8088}"

if ! [[ "$PORT" =~ ^[0-9]{1,5}$ ]] || [ "$PORT" -lt 1024 ] || [ "$PORT" -gt 65535 ]; then
  echo "CDBS_SITE_PORT inválida. Use uma porta entre 1024 e 65535." >&2
  exit 2
fi

if ! python3 - "$PORT" <<'PY'
import socket
import sys
try:
    with socket.create_connection(("127.0.0.1", int(sys.argv[1])), timeout=2):
        pass
except OSError:
    print(f"O CDBS não respondeu em 127.0.0.1:{sys.argv[1]}. Inicie primeiro com: bash start.sh", file=sys.stderr)
    raise SystemExit(1)
PY
then
  exit 1
fi

echo "Abrindo Cloudflare Quick Tunnel para os sites (porta $PORT)."
echo "O seu painel administrativo (porta 8089) não será exposto."
echo "O painel dos clientes vai junto, dentro do mesmo endereço: .../_painel/"
echo "Copie o endereço https://....trycloudflare.com que aparecer abaixo."
echo "(Modo manual: o CDBS não fica sabendo deste link. Prefira iniciar com bash start.sh e ligar o túnel pelo painel.)"
exec cloudflared tunnel --url "http://127.0.0.1:${PORT}"
