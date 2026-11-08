#!/usr/bin/env bash
# CDBS.OS — diagnóstico (só lê; não altera nada e não mostra senha, chave nem token).
# É o mesmo que: python3 cdbs.py doctor
# Uso: bash diagnostico.sh              relatório legível
#      bash diagnostico.sh --offline    sem testar a internet
#      bash diagnostico.sh --json       saída em JSON
#      bash diagnostico.sh --corrigir   aplica SÓ correções seguras e diz o que fez
# Mande o resultado quando pedir ajuda: ele não contém segredos.
cd "$(dirname "$0")" || exit 2
if ! command -v python3 >/dev/null 2>&1; then
  echo "[FALHA] Python não encontrado. Instale com: pkg install python -y"
  exit 1
fi
if [ ! -f cdbs.py ] || [ ! -f doctor.py ]; then
  echo "[FALHA] Esta pasta não tem os arquivos do CDBS (cdbs.py/doctor.py). Rode o script de dentro da pasta do CDBS."
  exit 1
fi
exec python3 cdbs.py doctor "$@"
