# Inventário técnico do CDBS.OS (Parte 1.1)

Feito em 2026-10-26, **antes** de qualquer mudança da 3.0, sobre o pacote que você enviou (`CDBS-Android-Cloudflare-PLANO-MESTRE-PARTES-1-2-3.zip`).
O código desse pacote é **idêntico** ao da Evolução 2.1 entregue em 23/10. Conferi arquivo por arquivo: as únicas diferenças são o `README.md` (1 parágrafo apontando o plano) e a pasta `docs/updates/` com as especificações.
**Linha de base:** a suíte existente rodou sobre essa cópia: **194 testes, OK, 77 s** (Python 3.13, contêiner Linux).

## 1. Módulos (antes da 3.0)

| Arquivo | Linhas | Papel |
|---|---|---|
| `cdbs.py` | 1 269 | Servidor: painel administrativo (`AdminHandler`, 127.0.0.1:8089) e servidor dos sites (`PublicHandler`, 127.0.0.1:8088), que também atende o painel do cliente (`/_painel`). Envio e validação de ZIP de site, sessões do admin (em memória), limites de conexão, prazo anti-slowloris, lembrete de backup |
| `catalog.py` | 468 | Lojas, produtos, fotos, senhas dos clientes (PBKDF2), sessões assinadas (HMAC com `data/secret.key`), loja "pendente" |
| `backup.py` | 1 951 | Central de backups: criar, validar, exportar e importar, restaurar com diário e reversão, recuperar loja removida, excluir, retenção manual, lembrete mensal |
| `backup_crypto.py` | 280 | Formato CDBK v1 (AES-256-GCM em blocos de 1 MiB + scrypt) |
| `web/` (index.html, app.js, app.css, backups.js, admin.js, catalog.js) | ~1 570 | Painel do administrador; `admin.js` (botão escondido) e `catalog.js` (leitura dos produtos) são servidos aos sites em `/_cdbs/` |
| `client/` | ~490 | Painel do cliente da loja |
| `start.sh` | 4 | `exec python3 cdbs.py` |
| `start-cloudflare.sh` | 65 | Quick Tunnel **só** para a porta dos sites |
| `atualizar.sh` | 111 | Atualização manual com cópia completa e `--voltar` |
| `diagnostico.sh` | 71 | Diagnóstico em shell (só leitura) |
| `tests/` (9 arquivos) | ~3 800 | 194 testes |

**Não existiam:** monitoramento das posições, notificações, WhatsApp, bot, supervisor/watchdog, atualizador pelo painel, histórico de auditoria, verificação de integridade do programa.

## 2. Rotas

**Painel (8089, só 127.0.0.1, Host conferido → 421):**

| Grupo | Rotas |
|---|---|
| Interface | `GET /`, `/app.css`, `/app.js`, `/backups.js` |
| Sessão | `GET /api/session`; `POST /api/setup`, `/api/login`, `/api/logout` |
| Sites | `GET /api/sites`; `POST /api/sites?slug=`; `DELETE /api/sites/<slug>` |
| Lojas | `GET /api/stores`; `POST /api/stores`, `/api/stores/<slug>`, `/api/stores/<slug>/password`; `DELETE /api/stores/<slug>` |
| Backups | `/api/backups/...`: overview, `_protecao`, `_retencao` (+preview/apply), por loja (lista, create, import, `<id>`, validate, protect, restore/summary, restore, delete/summary, delete, download) |

Escritas exigem `Origin` exato e sessão. A Central de backups exige também `X-CDBS-Request: 1`, `Content-Type` JSON (415) e limite de pedidos.

**Sites (8088, o único que vai para o túnel):**
- `GET/HEAD /<slug>/...` serve os arquivos do site;
- `/_api/<slug>/products.json` e `/_media/<slug>/<foto>` são públicos;
- `/_cdbs/catalog.js` e `/_cdbs/admin.js` são os ajudantes;
- `/_painel/...` é o painel do cliente (Origin igual ao Host).

O resto dá 404 ou 405.

## 3. Fluxos atuais

- **Início:** `start.sh` → `cdbs.py`. Faz `recover()` dos backups interrompidos e limpa temporários, inicia o lembrete (ao iniciar e a cada 6 h) e abre as duas portas. Não há sinal de vida, histórico nem supervisão: se o Android mata o processo, nada registra nem reinicia.
- **Publicação:** o ZIP é validado (caminhos, links, ocultos, extensões, 15/50 MB, 300 arquivos) numa pasta temporária e depois movido com `os.replace`. **Remover** apaga a pasta. Os backups continuam.
- **Restauração:** validação → espaço → backup de segurança (cifrado) → montagem temporária → troca com diário (o público recebe 503) → conferência → reversão se falhar.
- **Atualização:** só manual (`atualizar.sh`), exigindo `cdbs.py` **na raiz** do ZIP. Por isso ele **recusaria** o próprio ZIP que você enviou, que traz tudo dentro de `cdbs_package/`. Essa incompatibilidade estava apontada na especificação e foi corrigida na 3.0.
- **Túnel:** o usuário roda `start-cloudflare.sh` em outra sessão. O endereço só aparece no terminal: o CDBS não sabe qual é o link público.

## 4. Dados persistentes (nunca sobrescrever)

| Caminho | Conteúdo | Sensível |
|---|---|---|
| `.env` | portas, endereço público (na 3.0 também tokens) | **sim** (3.0) |
| `data/admin.json` | hash PBKDF2 da senha do painel | sim |
| `data/secret.key` | chave das sessões dos clientes | sim |
| `data/sites/<slug>/` | sites publicados | — |
| `data/catalogs/<slug>/` | `store.json` (hash da senha do cliente), `products.json`, fotos | sim (`store.json`) |
| `data/backups/` | backups `.cdbk`/`.zip`, registros, `protection.json` (verificador), retenção, lembrete | sim |
| `data/restore-tmp/` | trabalho temporário de restauração | — |

## 5. Riscos de regressão registrados antes de mexer

1. `cdbs.py` lê portas e endereço público **na importação**. Um `.env` errado derruba até o diagnóstico. Na 3.0 o `doctor` roda antes dessa leitura.
2. As sessões do admin ficam só em memória, então reiniciar desloga. Com reinícios do supervisor e atualizações pelo painel isso fica frequente; na 3.0 elas passam a ser guardadas (só o hash).
3. Os testes alteram variáveis globais do módulo (`DATA`, `SITES`, portas). Todo serviço novo precisa ser obtido por função ligada à pasta de dados atual (padrão `get_backup()`), senão os testes vazam estado entre si.
4. O servidor dos sites e o painel moram no mesmo processo e dividem travas em memória (catálogo, manutenção da restauração). Separá-los em processos exigiria travas entre processos. A decisão está registrada em `docs/ARQUITETURA-3.0.md`.
5. Nenhum script tinha teste automatizado (só `bash -n` manual).
6. Nada foi executado num Android real até hoje. Todos os resultados vêm do contêiner Linux.

## 6. Depois da 3.0

Este inventário retrata o sistema **antes** das mudanças, como pede o item 1.1 do plano.

O que existe agora está em [`ARQUITETURA-3.0.md`](ARQUITETURA-3.0.md):
- processos e módulos;
- rotas novas e dados novos;
- fluxos e segurança;
- decisões tomadas;
- rastreabilidade de cada item do plano.

Os riscos da seção 5 viraram estas decisões ou correções:

| Risco | O que foi feito |
|---|---|
| 1 | o `doctor` roda antes de ler as portas |
| 2 | sessões guardadas como hash |
| 3 | serviços obtidos por função ligada à pasta de dados; um teste antigo que escrevia nos dados reais foi isolado |
| 4 | decisão D1 |
| 5 | `tests/test_scripts.py` |
| 6 | continua valendo: os testes no aparelho seguem **pendentes** |

