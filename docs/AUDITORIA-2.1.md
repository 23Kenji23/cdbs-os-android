# Auditoria inicial da Evolução 2.1 (antes de qualquer alteração)

Base auditada: `CDBS-Android-Cloudflare.zip` da Evolução 2 (SHA-256 `1eb504a1c3424609a54379f59dd210d43cc386789ff8dd91fd10df083269e3da`, 25 arquivos, extraído numa cópia limpa). Arquivos lidos por inteiro: `backup.py`, `cdbs.py` (servidores, rotas, autenticação, limites), `catalog.py`, `web/backups.js`, `web/index.html`, `README.md`, `docs/BACKUP.md`, `docs/AUDITORIA-BACKUP.md`, `start.sh`, `start-cloudflare.sh`, testes.
Esta auditoria foi feita no contêiner Linux; **nada foi executado no Android**.

## Linha de base

| Comando | Resultado |
|---|---|
| `python3 -I -m unittest discover -s tests` (na cópia limpa do ZIP) | **89 testes OK em ~43 s** (registrado em `baseline-21.txt`) |
| Python / bibliotecas no contêiner | Python 3.13; `hashlib.scrypt` presente; pacote `cryptography` 50.0.1 presente |

## Diagnóstico

| # | Item verificado no código real | Status | Evidência / observação |
|---|---|---|---|
| 1 | Backups ficam fora da raiz pública | **PASS** | `data/backups/` ≠ `data/sites/`; o servidor público só serve `SITES/<slug>`; 404 testado |
| 2 | Rotas `/api/backups/*` exigem sessão | **PASS** | `_authorized()` antes de `_backups()`; 401 testado em todas |
| 3 | Validação de origem nos POST | **PASS (parcial)** | `Origin` exato (`127.0.0.1`/`localhost`:8089); ausente ⇒ 403. **Falta defesa em profundidade** (cabeçalho próprio, validação de `Host`, tipo de conteúdo) |
| 4 | Cookie de sessão | **PASS** | `HttpOnly; SameSite=Strict` |
| 5 | Limite de pedidos | **PASS (parcial)** | `BACKUP_GATE` 90/min nas rotas que alteram; **não há limite específico de tentativas de senha** (inexistente hoje porque não há senha de backup) |
| 6 | Servidor do painel só em 127.0.0.1:8089; sites 127.0.0.1:8088 | **PASS** | `LocalOnlyServer(("127.0.0.1", …))` |
| 7 | Concorrência por loja | **PASS** | `_active[slug]`; teste de duas operações |
| 8 | Falta de espaço recusa antes de gravar | **PASS** | testes de criar e de restaurar |
| 9 | Recuperação após reinício | **PASS (parcial)** | diário revertido e `.part` removidos; **FAIL: pasta temporária órfã** de uma restauração morta antes de gravar o diário **permanece** (reproduzido: `recover()` devolve 0 e a pasta continua em `restore-tmp/`) |
| 10 | Backups **sem criptografia** (arquivo lido por quem o obtiver) | **FAIL** | limitação declarada e confirmada: ZIP comum em `data/backups/<loja>/<id>.zip` |
| 11 | Exclusão de backup | **FAIL** | não existe (nem rota nem botão) |
| 12 | Retenção | **FAIL** | não existe política; só acumula |
| 13 | Recuperar loja removida | **FAIL (limitação real)** | com o catálogo apagado a restauração **pula** o catálogo (`catalog_skipped`) e o backup não tem como recriar a loja sem senha |
| 14 | Troca site/catálogo | **PASS com janela** | `os.rename` com diário; entre mover o antigo e colocar o novo o visitante recebe **404** (janela de milissegundos, sem aviso de manutenção) |
| 15 | Lembrete mensal | **PASS (parcial)** | calculado só quando o painel abre; **FAIL**: lojas nunca abertas no painel não começam a contar; não há verificação ao iniciar nem periódica |
| 16 | ZIP Slip, bomba, caminhos, segredos fora do backup | **PASS** | testes existentes (49) |
| 17 | Logs sem credenciais | **PASS** | `log_message` desligado nos dois servidores; testes de visões/registros |
| 18 | Instalação/atualização para Termux | **PASS (parcial)** | README descreve; **sem script de atualização reversível nem diagnóstico**; não há dependência externa hoje |
| 19 | Execução no Android real | **BLOCKED** | sem acesso ao aparelho |

## Decisões de projeto desta evolução

* **Biblioteca**: `cryptography` (AES-256-GCM) + `hashlib.scrypt` da biblioteca padrão. Não há criptografia moderna na biblioteca padrão e não se deve inventar uma. É a **primeira dependência externa** do projeto; por isso a Central continua funcionando sem ela (listar, verificar, baixar e restaurar legados) e **bloqueia explicitamente** a criação de backups novos, sem fingir proteção.
* **Senha de recuperação** definida pelo administrador, **nunca gravada**; só um verificador (resultado de scrypt) para evitar erro de digitação. Sem “porta dos fundos”.
* Retenção **automática fica desativada e sem executor** (BLOCKED — aguardando autorização). Existem política configurável, prévia e limpeza **manual** com confirmação.
* Loja recuperada volta **pendente** (invisível ao público) até o administrador gerar nova senha.
