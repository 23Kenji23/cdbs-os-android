# Auditoria — Fase 1 / Evolução 2: backups, restauração e exportação

Data: 2026-10-22. Feita **antes** de qualquer alteração de código, sobre o projeto real (CDBS.OS; a especificação diz CDBS.DEV, é o mesmo projeto).

## 1. Estado de cada item da Etapa A

| Item | Estado | Achado |
|---|---|---|
| Arquitetura | PASS | Python 3 só com biblioteca padrão; dois servidores locais (sites 8088, painel admin 8089); HTML/CSS/JS puros; dados em arquivos JSON/pastas, **sem banco de dados** |
| Onde ficam os sites | PASS | `data/sites/<nome>/` (já extraído). A raiz pública do servidor de sites **é só essa pasta** |
| Dados de cada loja | PASS | `data/catalogs/<nome>/`: `store.json` (nome, WhatsApp, **hash da senha do cliente**, época das sessões), `products.json`, `media/` (fotos) |
| Temporários | PASS | `data/sites/.cdbs-upload-*` (instalação de ZIP), `.nome.tmp` (escrita atômica) |
| Autenticação do admin | PASS | PBKDF2 310 mil rodadas em `data/admin.json` (0600); sessão em memória (cookie HttpOnly, SameSite=Strict, 12 h); `Origin` exato em POST/DELETE; bloqueio após 5 erros; rotas `/api/*` exigem sessão |
| Backup já existente | PASS (nada existe) | Não há backup, exportação de site ou versionamento |
| **Limpeza automática** | PASS | **Não existe nenhuma.** As únicas remoções são manuais: botão "Remover" do site (`shutil.rmtree`) e remover loja (`shutil.rmtree`) |
| Cotas | PASS | O código impõe: site 50 MB descompactado / 300 arquivos / ZIP de 15 MB; fotos do catálogo 60 MB por loja (2 MB por foto, 200 produtos). **A cota de 500 MB por loja e o limite de 10 lojas NÃO existem no código** |
| Comandos | PASS | Instalar/iniciar: `bash start.sh` (`python3 cdbs.py`), túnel: `bash start-cloudflare.sh`; testes: `python3 -m unittest discover -s tests`; parar: Ctrl+C |
| Linha de base de testes | PASS | **40 testes, 40 OK**, ~26 s (executados hoje antes de editar) |
| Evolução 1 implementada? | **FAIL (removida)** | Foi implementada (publicação na Netlify) e **removida a seu pedido** hoje. Não está no projeto. Cópia guardada fora do pacote. Esta evolução não depende dela |
| Acesso ao aparelho | BLOCKED | Não tenho acesso ao seu telefone: entrego o código e os comandos; o teste real no Android é seu |

## 2. Riscos de perda de dados e incompatibilidade

| # | Risco | Tratamento |
|---|---|---|
| R1 | **Remover site/loja apaga tudo sem volta** e hoje não há backup | Backups ficam em `data/backups/` (fora de `data/sites` e `data/catalogs`): remover site/loja **não apaga os backups**. Não mudo esse comportamento sem sua autorização (sugestão no final) |
| R2 | `store.json` guarda o **hash da senha** do cliente | O backup **não inclui** `store.json`; guarda só nome/WhatsApp (informativo). A restauração **nunca** troca a senha nem as sessões |
| R3 | O cliente edita o catálogo enquanto o backup/restauração roda | Usa a mesma trava do catálogo (`Catalog._lock`, já usada em todas as escritas) |
| R4 | Troca da pasta do site não é uma única operação do sistema | Diário de recuperação em disco + volta automática; ao iniciar, uma troca interrompida é revertida (versão anterior volta) |
| R5 | Android/Termux é encerrado no meio de uma operação | Operação em segundo plano com registro; ao iniciar, o que ficou "em andamento" vira FALHOU/INTERROMPIDO e a recuperação roda |
| R6 | Falta de espaço (telefone) | Mede espaço livre antes de criar e de restaurar; cancela **antes** de mexer no site; nada é apagado para liberar espaço |
| R7 | ZIP malicioso/gigante (importação e restauração processam ZIP) | Nomes validados (sem `..`, absolutos, links, duplicados), limite de arquivos e bytes, tamanho real medido ao ler (não confia no cabeçalho) |
| R8 | Backup só no telefone não protege contra perda do telefone | Exportação (download) e importação; aviso fixo no painel. O CDBS **não afirma** que existe cópia externa: só o administrador sabe que salvou o arquivo |
| R9 | Cota "500 MB/loja" proposta mas inexistente | **Não aplico cota nova.** Mostro o espaço dos sites e dos backups separadamente e documento a política (backups não entram em nenhuma cota; limite de segurança de 1 GB por backup só para evitar descontrole) |

## 3. Decisões de desenho

- **Formato**: um `.zip` por backup, com `manifest.json` (versão do formato, ID, loja, data, lista de arquivos com tamanho e **SHA-256**). Fica em `data/backups/<loja>/<id>.zip` (0600; pasta 0700) com um registro `<id>.json` ao lado (estado, validação, histórico).
- **Conteúdo**: arquivos do site (`data/sites/<loja>`) + catálogo (`products.json` + fotos). **Nunca**: `.env`, `store.json`/senhas, chaves, tokens, cookies, sessões, arquivos ocultos, links simbólicos.
- **Só vira "Backup concluído" depois de validado** (reabre o ZIP, confere inventário, tamanhos e hashes).
- **Restauração**: confirmação em 2 etapas → valida de novo → cria backup de segurança do estado atual (validado) → extrai em pasta temporária isolada → confere hashes → troca → confere o resultado → se algo falhar, volta ao estado anterior.
- **Lembrete**: calculado na hora em que o painel abre (não depende de tarefa agendada que o Android possa matar); aviso gravado no máximo 1 vez por dia por loja.
- **Sem apagar backups** (não há função de excluir nesta evolução).

## 4. Plano (mudanças pequenas e reversíveis)

Novo: `backup.py`, `web/backups.js`, `tests/test_backup.py`, docs. Alterados: `cdbs.py` (rotas `/api/backups/*`, recuperação ao iniciar), `web/index.html`, `web/app.js`, `web/app.css`, `.gitignore`, `README.md`. Sem migração: dados antigos não são tocados.

## 5. Fora do escopo

Módulo financeiro, suspensão automática, criptografia em repouso (sem biblioteca reconhecida na biblioteca padrão; documentado), agendamento automático, exclusão de backups, cota nova.
