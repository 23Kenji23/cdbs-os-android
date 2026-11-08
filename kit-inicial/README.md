# CDBS.OS — kit inicial de Loop Engineering

Este é o ponto de partida para **criar** o CDBS.OS com um agente de IA em loop. Não contém o produto pronto: contém as instruções, as especificações e a lista de tarefas para o agente construí-lo, uma tarefa por volta.

A técnica de loop (apelidada "Ralph") foi publicada por Geoffrey Huntley em julho de 2025. A ideia: ler sempre a mesma instrução, fazer uma tarefa, rodar os testes, commitar se passou, anotar se falhou, zerar a memória e recomeçar.

## Os 4 arquivos que o humano escreve

| Arquivo | Para quê |
|---|---|
| `PROMPT.md` | A instrução fixa de toda volta |
| `fix_plan.md` | A lista de tarefas com `[ ]` e `[x]`. O agente risca; você confere todo dia |
| `specs/` | O que o projeto deve fazer, em arquivos curtos. É daqui que sai a lista |
| `AGENTS.md` | Como rodar build e testes, para o agente não inventar comando |

Extras: `loop.sh` (o ciclo), `NOTAS.md` (o que falhou), `.gitignore`.

## Regras do loop
1. Uma tarefa por volta.
2. Procurar antes de criar.
3. Proibido placeholder.
4. Gate: testes, sintaxe, lint. Passou → commit. Falhou → registra em `NOTAS.md` e passa.
5. Quando não sobra `[ ]`, o loop imprime `DONE` e para.

## Como rodar
```sh
bash loop.sh                      # até 30 voltas
MAX_VOLTAS=5 bash loop.sh         # teste curto
touch PARAR                       # freio manual
```

## Segurança — leia antes de deixar rodando de madrugada
- Rode **numa sandbox** (VM ou container descartável), **com teto** de voltas. Nunca no celular com dados reais.
- `claude -p` em modo automático não pode pedir sua aprovação. Para o agente editar arquivos e rodar testes sem você, é preciso liberar permissões (`CLAUDE_FLAGS`, conforme a documentação do Claude Code). Faça isso só dentro da sandbox.
- Nunca coloque tokens, senhas ou `.env` preenchido neste kit.
- Confira `git log` e `fix_plan.md` toda manhã: o loop pode andar na direção errada.
