# PROMPT.md — instrução fixa de toda volta

Você está dentro de um loop. A cada volta sua memória começa zerada: tudo o que precisa saber está nos arquivos deste projeto.

1. Leia `AGENTS.md` (como rodar build e testes) e os arquivos de `specs/`.
2. Abra `fix_plan.md` e pegue o item `[ ]` mais importante (o primeiro da lista).
3. **Procure no código antes de criar.** Se já existe algo parecido, reaproveite.
4. Faça **só esse item**. Uma tarefa por volta.
5. Escreva o teste junto com o código. **Proibido placeholder**: nada de TODO, função vazia, dado inventado ou "implementar depois".
6. Rode o gate definido em `AGENTS.md` (testes, sintaxe, lint).
7. **Passou** → troque `[ ]` por `[x]` no item e faça commit com mensagem curta (`feat: ...` ou `fix: ...`).
8. **Falhou** → tente corrigir nesta volta. Se não resolver, troque `[ ]` por `[!]`, anote em `NOTAS.md` o que falhou e por quê, e siga em frente (registra e passa). Não marque `[x]` sem teste passando.
9. Se não existir mais nenhum `[ ]`, escreva `DONE` e pare.

Regras de segurança (valem sempre):
- Nunca escreva token, senha ou chave real em arquivo, teste ou log. Use valores obviamente falsos nos testes.
- O painel de administração só escuta em `127.0.0.1`. Nunca em `0.0.0.0`.
- Nunca execute texto recebido de fora (WhatsApp, formulário, arquivo) como comando de shell.
- Não afirme que algo funciona sem ter rodado o teste. Relate só resultado real.
