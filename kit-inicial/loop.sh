#!/usr/bin/env bash
# Loop Engineering — roda o PROMPT.md em ciclo.
# Cada volta: lê a mesma instrução, faz UMA tarefa, roda o gate, commita e zera a memória.
# Uso:   bash loop.sh
# Freio: crie um arquivo chamado PARAR nesta pasta e o loop termina ao fim da volta.
set -u
cd "$(dirname "$0")" || exit 2

MAX_VOLTAS="${MAX_VOLTAS:-30}"       # teto de voltas (nunca rode sem teto)
PAUSA="${PAUSA:-5}"                  # segundos entre voltas
CLAUDE_FLAGS="${CLAUDE_FLAGS:-}"     # veja o README antes de liberar permissões

command -v claude >/dev/null 2>&1 || { echo "comando 'claude' não encontrado" >&2; exit 3; }
[ -f PROMPT.md ] && [ -f fix_plan.md ] || { echo "faltam PROMPT.md ou fix_plan.md" >&2; exit 3; }

if [ ! -d .git ]; then
  git init -q -b main && git add -A && git commit -qm "kit inicial do loop" || true
fi

pendentes() { grep -c '^- \[ \]' fix_plan.md; }

for ((volta = 1; volta <= MAX_VOLTAS; volta++)); do
  if [ -f PARAR ]; then echo "PARAR encontrado. Loop encerrado."; exit 0; fi
  if [ "$(pendentes)" -eq 0 ]; then
    travados="$(grep -c '^- \[!\]' fix_plan.md)"
    echo "DONE (itens travados para revisar: $travados)"
    exit 0
  fi
  echo "=== volta $volta/$MAX_VOLTAS — pendentes: $(pendentes) — $(date '+%F %T') ===" | tee -a loop.log
  # shellcheck disable=SC2086
  cat PROMPT.md | claude -p $CLAUDE_FLAGS 2>&1 | tee -a loop.log
  sleep "$PAUSA"
done

echo "Teto de $MAX_VOLTAS voltas atingido. Pendentes: $(pendentes). Revise fix_plan.md e NOTAS.md."
