#!/usr/bin/env bash
# CDBS.OS — inicia o CDBS pelo supervisor (o "guardião" que vigia o servidor, o túnel e as atualizações).
# Uso: bash start.sh               inicia (Ctrl+C para parar)
#      bash start.sh --verificar   só confere se dá para iniciar, sem iniciar
set -u
cd "$(dirname "$0")" || exit 2

fail() { echo "CDBS.OS não iniciou: $*" >&2; exit 3; }

command -v python3 >/dev/null 2>&1 || fail "Python não encontrado. Instale com: pkg install python -y"
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
  || fail "Python muito antigo ($(python3 --version 2>&1)). Atualize com: pkg upgrade python"
for f in cdbs.py supervisor.py; do
  [ -f "$f" ] || fail "o arquivo $f não está nesta pasta ($(pwd)). Extraia o ZIP do CDBS de novo."
done
if ! python3 -c 'import cryptography' >/dev/null 2>&1; then
  echo "Aviso: sem a biblioteca de criptografia os backups novos ficam bloqueados. Instale: pkg install python-cryptography -y" >&2
fi

if [ "${1:-}" = "--verificar" ]; then
  echo "Verificação OK: dá para iniciar com bash start.sh"
  exit 0
fi

# Pede ao Android para não adormecer o Termux (aparece uma notificação). Ajuda, mas não garante 24 h.
if command -v termux-wake-lock >/dev/null 2>&1; then
  termux-wake-lock >/dev/null 2>&1 || true
fi

exec python3 -u supervisor.py run
