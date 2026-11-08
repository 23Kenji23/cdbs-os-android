# Central de backups (Fase 1, Evolução 2 + 2.1)

Guia de uso. Tudo roda **só no seu celular**, sem serviço externo. Instalação e testes no aparelho: [`TERMUX.md`](TERMUX.md). Detalhes da proteção: [`CRIPTOGRAFIA.md`](CRIPTOGRAFIA.md).

## O que é

Uma seção do **painel privado** (porta 8089, nunca exposta na internet). Para cada loja (site + catálogo) ela:

1. cria uma cópia **criptografada** e conferida do **site** e do **catálogo** (produtos e fotos);
2. confere a integridade (SHA-256 de cada arquivo + inventário) ao criar e de novo antes de restaurar ou baixar;
3. deixa **baixar** e **importar** cópias;
4. **restaura** com confirmação, backup de segurança automático e reversão se algo falhar;
5. **recupera uma loja removida**, que volta desativada até você gerar senha nova;
6. **exclui** backups só quando você manda, com confirmação;
7. mostra o espaço de sites, catálogos e backups, e avisa quando uma loja passa 30 dias sem backup validado.

### O que entra e o que nunca entra num backup

| Entra | Nunca entra |
|---|---|
| Arquivos públicos do site (HTML, CSS, JS, imagens) | Senha/hash do cliente (`store.json`), senha do painel (`admin.json`), `secret.key` |
| Produtos (`products.json`) e fotos | Arquivos de segredo dentro do site (`.env*`, `*.pem`, `*.key`, `id_rsa*` …) |
| Nome, WhatsApp e data de criação da loja | A senha de recuperação dos backups e a chave derivada dela |

Restaurar **não muda a senha do cliente**, e nenhum backup carrega credenciais.

## Onde ficam os arquivos

```
data/backups/<loja>/<id>.cdbk     backup protegido (2.1)          — arquivo 0600, pasta 0700
data/backups/<loja>/<id>.zip      backup legado, sem criptografia (Evolução 2)
data/backups/<loja>/<id>.json     registro da operação (estado, tempos, erro; sem senha nem caminho do aparelho)
data/backups/protection.json      verificador da senha de recuperação (NÃO é a senha)
data/backups/retention.json       política de retenção escolhida (só alimenta a prévia manual)
data/backups/reminder-status.json última verificação do lembrete
data/backups/events.json          eventos (lembretes, exclusões, falhas de espaço)
data/restore-tmp/<operação>/      área de trabalho de uma restauração (apagada ao terminar ou ao iniciar o CDBS)
```

Tudo fica **fora** de `data/sites/`, então nada disso é servido ao público. O servidor público (8088) não tem rota de backup, e os testes conferem isso.

## Como usar

### Primeira vez: senha de recuperação
Antes do primeiro backup, defina a **senha de recuperação** em "Proteção dos backups". Ela é pedida para criar, verificar por inteiro, restaurar, importar e proteger backups. **Não existe recuperação dessa senha.** Veja como guardá-la em [`CRIPTOGRAFIA.md`](CRIPTOGRAFIA.md).

### Criar um backup
**Criar backup**, depois digite a senha. O estado passa por CRIANDO → VALIDANDO → CONCLUÍDO, com selo **CRIPTOGRAFADO**. O CDBS confere o conteúdo **antes** de cifrar e confere o arquivo cifrado **depois**.

### Verificar integridade
**Verificar integridade** oferece duas opções:
* **Verificar tudo** (com senha): abre o backup e confere cada arquivo;
* **Só o arquivo (sem senha)**: confere tamanho e impressão digital. Isso **não prova** que o conteúdo abre.

Se não conferir, o backup vira **INVÁLIDO** e deixa de poder ser restaurado ou baixado. Nada é apagado.

### Baixar e importar
**Baixar cópia** entrega o `.cdbk` (continua protegido fora do celular). Em **Importar**, escolha um `.cdbk` (pede a senha) ou um `.zip` legado. Só entra backup íntegro **desta mesma loja**.

### Restaurar
1. **Histórico e restauração** → **Restaurar…** → leia o resumo (o que volta, espaço necessário, se o backup de segurança será protegido).
2. Digite a senha de recuperação e o nome da loja, depois toque em **Restaurar agora**.
3. O CDBS valida → confere espaço → **cria um backup de segurança protegido do estado atual** → monta tudo numa pasta temporária → troca → confere.
4. **Durante a troca**, quem abrir o site recebe "em manutenção, tente em alguns segundos" (HTTP 503), nunca uma página pela metade. Alterações do cliente no painel esperam a troca terminar.
5. Se a troca ou a conferência falhar, a versão anterior é recolocada (**REVERTIDO**). Se o app for fechado no meio, ao iniciar de novo o CDBS desfaz pelo diário e limpa pastas temporárias órfãs.
6. Para desfazer uma restauração que deu certo, restaure o backup **"Segurança (antes de restaurar)"**.

### Recuperar uma loja removida
Se a loja foi removida do catálogo, os backups dela continuam na Central ("Loja removida — os backups continuam aqui…"). Para recuperar:
1. **Recuperar loja…** no backup desejado, depois marque que entende as condições e digite a senha e o nome.
2. Voltam site, produtos, fotos, nome e WhatsApp. A loja volta **DESATIVADA**: invisível ao público, e o cliente não entra (senhas e sessões antigas **não** voltam).
3. No painel da loja, toque em **Gerar senha e ativar** e passe a senha nova ao cliente.

Se, no meio do caminho, alguém criar uma loja nova com o mesmo nome, a recuperação **para** e não substitui essa loja ("Já existe uma loja com este nome").

### Excluir um backup (sempre manual)
**Excluir…** num backup CONCLUÍDO ou INVÁLIDO → leia o aviso → digite o identificador curto do backup → **Excluir definitivamente**.
* Excluir o **último backup validado** da loja exige marcar uma confirmação extra.
* Não dá para excluir um backup que está em uso por uma operação.
* O backup some da lista. O registro (`<id>.json`) é mantido internamente como EXCLUÍDO e a exclusão fica anotada em `events.json`, para você saber o que aconteceu.

### Espaço e retenção
A seção **Espaço e retenção de backups** mostra sites, catálogos, backups (protegidos e legados) e o espaço livre. A **política de retenção** pode ser:

| Opção | Efeito na prévia |
|---|---|
| Manter tudo (padrão) | nada é sugerido |
| Manter só os N mais recentes por loja (N ≥ 1) | os mais antigos viram candidatos |
| Manter só os últimos N dias (N ≥ 7) | os mais velhos viram candidatos |

O último backup validado de cada loja **nunca** é candidato. **Ver o que seria apagado** mostra a lista e o total. **Apagar estes backups** exige digitar `APAGAR` e só apaga o que estava na prévia e continua elegível. **Não existe exclusão automática**: ela está **BLOCKED, aguardando sua autorização**, e o código não tem executor agendado.

### Lembrete mensal
O CDBS verifica **todas** as lojas **ao iniciar e a cada 6 horas** enquanto estiver rodando. Lojas com 30 dias ou mais sem backup validado aparecem no aviso da Central, com o aviso registrado no máximo 1 vez por dia. A Central mostra quando foi a última verificação. **Não é notificação do Android**: com o Termux fechado ou o celular desligado, nada roda.

## Política de espaço

* Backups não entram em cota de loja e o CDBS **não impõe cota** (a ideia de 500 MB por loja **não está ativa**).
* Limite de segurança por backup: 1 GB e 5.000 arquivos.
* Antes de criar, restaurar ou importar, o CDBS confere o espaço livre e **recusa antes de gravar**, se não couber.
* **Nada é apagado automaticamente.**
* Backup só no celular **não protege** contra perda do aparelho: baixe cópias e guarde fora.

## Limites conhecidos

* A troca na restauração não é atômica no sistema de arquivos (são duas pastas). A janela é coberta pelo aviso de manutenção (503) e pelo diário de reversão.
* A senha de recuperação não pode ser trocada nesta versão.
* Exclusão automática por retenção: **BLOCKED**, aguardando autorização.
* Nada desta versão foi executado num Android real ainda (veja a tabela no fim de [`TERMUX.md`](TERMUX.md)).
* Integridade prova que o arquivo é o mesmo que foi criado. Não prova que o site funciona.
* Uma operação por loja por vez. Lojas diferentes podem rodar ao mesmo tempo.

## Testes automáticos

```sh
python3 -I -m unittest discover -s tests
```

Os arquivos da 2.1:
- `test_crypto.py`: formato, senha errada, adulteração, corte, reordenação, parâmetros hostis;
- `test_protecao.py`: senha de recuperação, legados, migração, importação;
- `test_exclusao.py`: exclusão, último validado, retenção manual;
- `test_recuperacao.py`: loja removida, falhas em cada etapa, interrupção, concorrência, 503;
- `test_seguranca21.py`: lembrete e auditoria das rotas.

Os da Evolução 2 seguem em `test_backup.py`.
