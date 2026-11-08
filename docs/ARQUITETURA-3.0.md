# Arquitetura do CDBS.OS 3.0: como está organizado e por quê

Este documento explica:
- quais **processos** rodam e o que cada um faz;
- onde ficam os **dados**;
- como funcionam os **fluxos** principais;
- o que protege o sistema;
- as **decisões** tomadas, com motivo e alternativa;
- a **rastreabilidade** do plano mestre: cada item virou o quê e qual teste o cobre.

O inventário feito **antes** de mexer está em [`INVENTARIO.md`](INVENTARIO.md).

## 1. Processos

```
bash start.sh   (confere Python, arquivos e biblioteca de criptografia; pede termux-wake-lock)
 └─ supervisor.py run ─────────── o "guardião": não serve página nenhuma, cuida de quem serve
     │   • uma instância só (trava flock em data/system/supervisor.lock)
     │   • lê pedidos em data/system/control/requests/ e grava resultados em .../results/ (lista fixa de operações)
     │   • estado e sinal de vida em data/system/supervisor.json (a cada ~5 s; "vivo" = sinal com menos de 1 min)
     │
     ├─ filho: python3 cdbs.py serve ─── o servidor (UM processo, várias linhas de execução)
     │    ├─ PAINEL  127.0.0.1:8089   seu painel e as APIs /api/...         ← só no celular, NUNCA no túnel
     │    ├─ SITES   127.0.0.1:8088   /<site>/, /_api, /_media, /_cdbs,     ← o ÚNICO que vai para o túnel
     │    │                            /_painel (painel do cliente) e /_whatsapp/webhook
     │    ├─ linha "monitor":       10 posições, rodadas alinhadas ao relógio
     │    ├─ linha "notificações":  fila de envio pelo WhatsApp (tentativas limitadas)
     │    ├─ linha "bot":           fila de mensagens recebidas pelo webhook
     │    ├─ linha "lembrete":      backups atrasados (desde a 2.1)
     │    └─ se o supervisor sumir (CDBS_SUPERVISOR_PID), o servidor se encerra sozinho: não sobra órfão
     │
     └─ filho opcional: cloudflared tunnel --url http://127.0.0.1:8088 --metrics 127.0.0.1:20241
          • o link https://….trycloudflare.com é lido da saída do cloudflared
          • "conectado" vem do endereço /ready das métricas do próprio cloudflared (200 = conectado)
          • modo nomeado: `cloudflared tunnel run` com o token pelo AMBIENTE (não aparece na lista de processos)
```

**Saúde do servidor.** A cada 15 s, o supervisor chama:
- `GET /api/health` no painel, que responde `{ok, version, started_at, pid}`;
- `GET /_cdbs/ping` no servidor dos sites.

Três falhas seguidas contam como **travado**: o supervisor encerra e reinicia o servidor.

Se o servidor cair, o reinício segue espera crescente: 2 s, 5 s, 15 s, 30 s, 1 min, 2 min, 5 min. Depois de **6 falhas em 15 min**, o supervisor **para de tentar** e fica em "parado_por_falhas", com o motivo registrado. Nunca entra em laço.

O túnel segue a mesma regra: espera crescente, limite de 8 falhas em 30 min.

## 2. Módulos

| Arquivo | Responsabilidade | Testes |
|---|---|---|
| `cdbs.py` | servidor (painel + sites), rotas, sessões do painel (persistidas como hash), publicação e remoção (guardada) de sites, webhook | `test_cdbs`, `test_fixes`, `test_seguranca21`, `test_base30`, `test_servico30`, `test_painel30` |
| `catalog.py` | lojas, produtos, fotos, senhas dos clientes | `test_catalog` |
| `backup.py`, `backup_crypto.py` | Central de backups (2.1) e formato CDBK | `test_backup`, `test_crypto`, `test_recuperacao`, `test_exclusao`, `test_protecao` |
| `supervisor.py` | guardião: servidor, túnel, pedidos, instalação de atualizações, autoteste | `test_supervisor` (com `fake_cloudflared.py`) |
| `control.py` | pedidos ao supervisor por arquivos: lista permitida, validade de 5 min, resultado real | `test_supervisor`, `test_bot`, `test_painel30` |
| `site_monitor.py` | 10 posições, verificação HTTP real (local e pública), estados, agendamento, histórico, relatório | `test_monitor` |
| `ops.py` | o que o painel e o bot podem fazer: visão geral, verificar, pausar/retomar, atribuir posição, pedir ao supervisor, textos de status | `test_painel30`, `test_bot` |
| `publication.py` | pausa de publicação: o público recebe 503, nada é apagado | `test_painel30` |
| `notify.py` | central de notificações: histórico local primeiro, agrupamento, espera, recuperação, janela de 24 h, tentativas, status de entrega | `test_notify` |
| `whatsapp.py` | cliente da **Cloud API oficial** (texto, modelo, lista, botões), assinatura do webhook, aperto de mão, tradução dos erros da Meta | `test_whatsapp` |
| `bot.py` | `!Link` (8 opções), `!Status`, `!Relatorio`, `!Ajuda`, `!Cancelar`; autorização, sessões, confirmação, idempotência, limites | `test_bot` |
| `updater.py`, `versioninfo.py` | validação do pacote, cópia conferida do programa, instalação, volta, diário, recuperação de interrupção; manifesto SHA-256 | `test_updater`, `test_supervisor`, `test_scripts` |
| `settings.py`, `config.py` | configurações (painel + `.env`, que vence), credenciais só-gravação, leitura do `.env` sem executar nada | `test_painel30`, `test_base30`, `test_doctor` |
| `audit.py` | histórico de eventos (`audit.jsonl`) com máscara de segredos e rotação, travado entre processos | `test_base30` |
| `lifecycle.py` | sinal de vida (detecta encerramento inesperado pelo Android) | `test_servico30` |
| `doctor.py` | diagnóstico (`cdbs.py doctor`), com verificações dos módulos 3.0 | `test_doctor` |
| `sysutil.py` | JSON atômico 0600, trava flock, máscaras (telefone, segredo), limpeza de texto para registro | `test_base30` |
| `web/ops.js`, `web/system.js` | painel 3.0: visão geral, posições, sites removidos, histórico, notificações, credenciais, bot, servidor, túnel, atualizações | `tests/e2e/ops.js` |
| `start.sh`, `atualizar.sh`, `diagnostico.sh`, `start-cloudflare.sh` | scripts do Termux | `test_scripts` |
| `tools/montar_pacote.py`, `tools/cobertura.py` | montar o ZIP com `VERSION.json`; medir cobertura | `test_updater`, `test_scripts` (pacotes montados nos testes) |

## 3. Dados

Tudo o que é do usuário fica em `data/`, que é privada (0700).

| Caminho | Conteúdo | Quem grava |
|---|---|---|
| `data/sites/`, `data/catalogs/`, `data/backups/`, `admin.json`, `secret.key` | o que já existia (2.1) | servidor |
| `data/admin-sessions.json` (0600) | sessões do painel: **só o hash** do token, no máximo 20 | servidor |
| `data/system/audit.jsonl` (0600) | histórico de eventos, com segredos mascarados; gira em 512 KB × 4 arquivos | servidor e supervisor (trava entre processos) |
| `data/system/settings.json` (0600) | configurações do painel (nada secreto) | servidor |
| `data/system/secrets.json` (0600) | credenciais do WhatsApp e token do túnel nomeado. **Nunca** devolvidas pela API | servidor |
| `data/system/supervisor.json`, `tunnel.json`, `tunnel.log`, `*-heartbeat.json` | estado do supervisor, do túnel e sinais de vida | supervisor e servidor |
| `data/system/control/requests` e `results` | pedidos ao supervisor e os resultados (os 200 mais recentes) | painel/bot → supervisor |
| `data/system/publication.json` | sites pausados | servidor |
| `data/system/bot-seen.json`, `bot-pending.json` | mensagens já processadas (7 dias, até 2000) e pedidos do bot esperando resultado | servidor |
| `data/monitoring/` | mapa das 10 posições, última verificação, últimas 300 rodadas | servidor |
| `data/notifications/` | notificações e estado (espera, janela de 24 h por número, cursor do histórico do supervisor) | servidor |
| `data/updates/` | pacotes recebidos (`staging/`), cópias conferidas do programa (`versions/`, as 2 mais recentes), diário e histórico | servidor e supervisor |
| `data/removidos/sites/` | sites removidos, guardados até recuperar ou apagar de vez | servidor |

Atualizações **nunca** tocam em `data/` nem no `.env`. Os testes de atualização conferem isso **byte a byte**.

## 4. Fluxos principais

**Pedido ao supervisor.** Exemplos: "reiniciar o servidor", "ligar o túnel", "instalar a atualização".
1. O painel ou o bot grava `requests/<id>.json` com uma operação de `control.OPERATIONS`.
2. O supervisor executa **um de cada vez** e grava `results/<id>.json` com `ok`, `falha` ou `recusado` e a mensagem real.
3. Quem pediu acompanha o resultado. O painel usa `/api/control/<id>`; o bot guarda o pedido em disco e avisa quando o resultado chega, mesmo que o servidor tenha sido reiniciado pelo próprio pedido.
4. Pedido com mais de 5 min é recusado como **expirado**. Operação fora da lista é **recusada**.

**Rodada de verificação.**
1. Trava de rodada: duas rodadas nunca se sobrepõem. Um pedido feito durante uma rodada entra na fila.
2. Cada posição é verificada em paralelo (até 4), com prazo próprio e 2 tentativas.
3. O resultado é gravado em `last_report.json` e `history.jsonl`.
4. A central de notificações recebe o relatório e as mudanças de estado.

**Notificação.**
1. `emit` grava **primeiro no histórico local**, com status `pendente`, `desativado`, `agrupado` ou `registrado`.
2. A linha de envio pega as pendentes:
   - **janela de 24 h aberta** para aquele número: texto livre;
   - **janela fechada**: modelo aprovado;
   - **sem modelo**: `bloqueado`.
3. Em falha temporária, tenta de novo após 30 s e 2 min; depois, `falhou`. Em falha definitiva (ex.: token inválido), para na hora.
4. O status final (`enviado`, `entregue`, `lido`, `falhou`) chega pelo webhook e **nunca regride**.

**Webhook → bot.**
1. `POST /_whatsapp/webhook` no servidor dos sites.
2. Confere limite de pedidos, tamanho (512 KB) e assinatura `X-Hub-Signature-256`. **Sem assinatura válida, o corpo nem é interpretado.**
3. Mensagens vão para a fila do bot; a resposta à Meta sai em milissegundos.
4. O bot processa nesta ordem: número de envio certo → já vista? → bot ligado? → número autorizado? → atrasada? → limite → comando.

**Atualização.**

| Etapa | Onde | O que faz |
|---|---|---|
| RECEBIDO → VALIDANDO | servidor | validação estática; nada é executado |
| COPIA_DE_SEGURANCA | supervisor | cópia conferida por SHA-256 |
| PARANDO → INSTALANDO | supervisor | troca atômica por arquivo; o manifesto vai por último |
| INICIANDO → VERIFICANDO | supervisor | saúde com a versão nova + autoteste do supervisor novo |
| CONCLUIDO, ou REVERTENDO → REVERTIDO | supervisor | — |

Se o Android interromper, o próximo `start.sh` lê `journal.json` e termina a volta **antes** de iniciar o servidor.

**Remover site.** `os.replace` da pasta para `data/removidos/sites/<id>/site`: atômico, no mesmo disco. O registro é gravado antes. Recuperar faz o caminho inverso, e recusa se já houver um site com o mesmo nome. Apagar de vez exige **APAGAR** digitado.

## 5. Segurança

- **Painel só local.** Ele escuta em 127.0.0.1:8089 e confere o `Host` (421 se for outro): isso protege contra *DNS rebinding*. Toda escrita exige:
  - sessão (cookie `HttpOnly`, `SameSite=Strict`);
  - `Origin` exato;
  - nas rotas novas e na Central de backups, também o cabeçalho `X-CDBS-Request: 1`, `Content-Type` JSON (415 se não for) e limite de 60 escritas por minuto.

  O túnel aponta **só** para 8088; a 8089 nunca é exposta.
- **Servidor público.** Só serve:
  - os sites;
  - as rotas públicas do catálogo;
  - o painel do cliente (com a senha própria do cliente);
  - o webhook.

  Nenhuma rota `/api/...` do painel existe nele (os testes conferem 404).
- **Webhook.**
  - assinatura HMAC-SHA256 com a chave do app (comparação em tempo constante);
  - aperto de mão só com o token de verificação;
  - 512 KB por evento e 300 eventos por minuto;
  - recusas registradas no histórico no máximo uma vez por minuto, para não encher o histórico.
- **Bot.**
  - lista de números (administrador / só consulta);
  - número desconhecido não recebe resposta;
  - mensagem processada uma vez só (idempotência em disco);
  - mensagem com mais de 10 min é ignorada;
  - 12 comandos por minuto e 60 por hora;
  - sessões de 5 min e confirmação de 2 min, com código no botão;
  - **cada opção é uma função fixa: nenhum texto vira comando de shell.**
- **Supervisor.**
  - pedidos só da lista `control.OPERATIONS`, por arquivos numa pasta 0700;
  - uma instância (flock);
  - o PID é conferido em `/proc/<pid>/cmdline` antes de agir, por causa da reutilização de PID pelo Android;
  - limpa processos órfãos dele mesmo ao iniciar.
- **Segredos.**
  - ficam em `secrets.json` (0600) ou no `.env`;
  - são gravados pelo painel e **nunca** devolvidos; o painel mostra só "configurado ••••1234";
  - `sysutil.scrub` mascara token, senha, cookie, códigos longos e telefones em tudo que vai para o histórico, o registro e as mensagens de erro;
  - não entram nos backups da Central nem no pacote: `montar_pacote.py`, `updater.py` e `atualizar.sh` recusam arquivos com nome de segredo;
  - o teste de navegador procura o token e a chave em **todos** os arquivos de `data/` (fora o `secrets.json`) e não acha.
- **Pacotes de atualização.**
  - validação estática;
  - recusa `..`, caminhos absolutos, links, repetidos, segredos e `data/`;
  - limites de tamanho;
  - SHA-256 por arquivo;
  - **nenhum script do pacote é executado na validação.** Depois de instalado, o próprio programa novo é iniciado e conferido.

## 6. Decisões de arquitetura

| # | Decisão | Por quê | Alternativa considerada / consequência |
|---|---|---|---|
| D1 | **Painel e servidor dos sites continuam no MESMO processo.** O isolamento vem de uma linha de execução por pedido, da limitação de conexões e do reinício pelo supervisor | o catálogo e a manutenção das restaurações usam travas **em memória** compartilhadas pelos dois lados. Separar exigiria travas entre processos em todo o catálogo e no backup: muito risco de regressão no que já funciona | processos separados. Consequência aceita: se o **processo** cair, os dois caem juntos, e o supervisor reinicia em segundos. Um site com problema não derruba os outros: são arquivos estáticos, cada pedido isolado. Testado: pausar um site não afeta os outros; uma posição com erro não impede as demais |
| D2 | **Supervisor é outro processo e conversa por arquivos** | não abre porta nova, não passa por shell e sobrevive ao servidor (é ele quem reinicia e atualiza) | conversar por porta HTTP local: mais superfície de ataque, sem ganho |
| D3 | **"Iniciar/parar publicação" = retomar/pausar** | site estático não tem processo próprio para iniciar ou parar. Pausar devolve 503 "temporariamente fora do ar" e não apaga nada | apagar e republicar: destrutivo |
| D4 | **Estados do plano → estados do CDBS** | ativo = 🟢 ATIVO · indisponível/erro = 🔴 FORA_DO_AR (com motivo) · parado = ⏸ PAUSADO · configuração ausente = 🔴 FORA_DO_AR "pasta do site não existe" · vaga = ⚪ VAGO · e dois a mais, porque fazem diferença para o dono: 🟠 SO_LOCAL (site bom, túnel ruim) e 🟡 INCONCLUSIVO (sem internet ou tempo esgotado: não acusa o site) | "iniciando" não se aplica (não há processo por site) |
| D5 | **Webhook no servidor dos sites (8088)**, protegido por assinatura | é a única porta que já vai para o túnel. Uma porta só para o webhook exigiria outro túnel | Quick Tunnel muda o link a cada reinício: é preciso recadastrar na Meta. Recomendado: túnel nomeado + `CDBS_PUBLIC_BASE` |
| D6 | **Só a API oficial (Cloud API)** | regra do plano; automação do WhatsApp Web arrisca banimento do número | a API exige conta na Meta, modelos aprovados e tem custo por mensagem (documentado) |
| D7 | **Atualização sem cópia de `data/`**; a cópia é do **programa** (conferida por SHA-256) | atualizações nunca tocam em `data/` (testado byte a byte) e copiar `data/` dobraria o espaço a cada atualização. O `atualizar.sh` (caminho manual, pensado para a 2.1) continua fazendo cópia completa | as cópias do programa guardam as 2 mais recentes. Isso é diferente dos **backups do usuário**, que nunca são apagados sozinhos |
| D8 | **Pacote sem assinatura digital**; integridade pelo manifesto SHA-256 | sem infraestrutura de chaves do usuário hoje | o SHA-256 pega arquivo corrompido ou trocado, mas **não prova a origem**. Instale só pacotes baixados de onde confia. Futuro: assinatura com chave do dono |
| D9 | **`supervisor.py` novo vale no próximo `start.sh`** (o servidor novo vale na hora) | o supervisor não pode se substituir enquanto supervisiona a própria atualização | ele é validado com `--selftest` durante a atualização |
| D10 | **`X-CDBS-Request` só na Central de backups e nas rotas 3.0** | as rotas antigas (publicar, lojas) continuam com `Origin` + cookie `SameSite=Strict`, como na 2.1, para não quebrar o que já funcionava | — |
| D11 | **Sessões do painel guardadas (só o hash)** | com reinícios pelo supervisor e atualizações, perder a sessão a cada reinício seria frequente | "Sair" apaga a sessão; no máximo 20 sessões |
| D12 | **Remover site guarda os arquivos** (`data/removidos/`), nunca apaga sozinho | item 1.3 do plano (cópia antes de operação destrutiva). Também permite voltar a versão anterior de um site | ocupa espaço até o dono apagar de vez; o diagnóstico mostra quanto |
| D13 | **Relatório por modelo de 12 variáveis**, com alternativa de 3 | a Meta não aceita quebra de linha dentro de variável; 12 variáveis dão uma linha por posição. Se a Meta recusar por "variáveis demais", o resumido de 3 funciona com a mesma verificação | a escolha 12/3 precisa ser igual ao modelo aprovado |
| D14 | **Rodadas alinhadas ao relógio e no máximo um relatório por horário** | relatório previsível (:00, :30) e sem duplicar depois de reinício | — |
| D15 | **Com o relatório desligado, quedas das rodadas agendadas viram alerta próprio** | quem desliga o relatório para economizar ainda precisa saber das quedas (lacuna encontrada e corrigida nesta versão) | — |
| D16 | **Cobertura medida com ferramenta própria** (`tools/cobertura.py`, `sys.monitoring` do Python 3.12+) | o `coverage.py` não pôde ser instalado no ambiente de teste (PyPI bloqueado) | mede só o código que roda no processo do teste: o que roda em subprocessos (scripts, supervisor real) é testado, mas não entra na porcentagem |

## 7. Rastreabilidade do plano mestre

| Item do plano | O que foi feito | Onde | Testes |
|---|---|---|---|
| 1.1 Inventário | módulos, rotas, fluxos, dados persistentes e riscos **antes** de mexer | `docs/INVENTARIO.md` | — |
| 1.2 Testes em todos os setores | suíte existente mantida, mais 11 arquivos novos, mais 3 testes de navegador; falsos para WhatsApp, Cloudflare, rede e processos | `tests/`, `tests/e2e/` | todos (relatório final) |
| 1.3 Backup, restauração, recuperação | Central 2.1 mantida; cópia conferida do programa antes de atualizar; cópia completa no `atualizar.sh`; volta automática; recuperação de atualização interrompida; site removido guardado | `backup.py`, `updater.py`, `atualizar.sh`, `cdbs.py` | `test_backup`, `test_recuperacao`, `test_updater`, `test_scripts`, `test_painel30` |
| 1.4 Auditoria | `audit.jsonl` com categoria, ação, resultado, ator, alvo, motivo, ref; máscara; rotação; filtros no painel | `audit.py`, `web/ops.js` | `test_base30`, `test_painel30`, e2e |
| 1.5 `doctor` | ambiente, comandos, bateria, integridade do programa, configuração, permissões, espaço, backups, processos, túnel, 10 posições, WhatsApp, bot, atualizações, conectividade; `--json`, `--offline`, `--corrigir` | `doctor.py`, `diagnostico.sh` | `test_doctor`, `test_scripts` |
| 1.6 Android/Termux | dependências e limites documentados; sinal de vida e "encerramento inesperado"; mensagens de "não consegue iniciar" | `docs/ANDROID.md`, `lifecycle.py`, `start.sh`, `cdbs.py` | `test_servico30`, `test_scripts` · **testes no aparelho: PENDENTES** |
| 2.1 Monitoramento real | 10 posições fixas, HTTP real local e público, estados, última verificação, duração, motivo | `site_monitor.py` | `test_monitor` |
| 2.2 Watchdog e agendamento | supervisor com saúde, reinício limitado, travamento; rodadas a cada 30 min sem sobreposição | `supervisor.py`, `site_monitor.py` | `test_supervisor`, `test_monitor` |
| 2.3 Notificações e WhatsApp | central local, agrupamento, espera, recuperação, tentativas, janela de 24 h, modelos, status de entrega; Cloud API | `notify.py`, `whatsapp.py` | `test_notify`, `test_whatsapp`, e2e · **envio real: PENDENTE** |
| 2.4 Painel operacional | visão geral, posições com ações, túnel, última notificação, último backup, atualização; confirmação e resultado real | `web/` | `test_painel30`, e2e `ops.js` |
| 2.5 Atualizador com rollback | ver seção 4; recuperação não depende do processo substituído (é o supervisor) | `updater.py`, `supervisor.py` | `test_updater`, `test_supervisor`, e2e |
| 2.6 Concorrência e isolamento | flock (supervisor, atualização, histórico), trava de rodada, pedidos um por vez, `UPLOAD_LOCK`; trava solta sozinha se o processo morre | `sysutil.py`, `supervisor.py`, `updater.py`, `site_monitor.py` | `test_supervisor`, `test_updater`, `test_monitor`, `test_base30` |
| 3.1–3.3 Comandos, menu, confirmação | `!Link` (8 opções, lista + texto numerado), `!Status`, `!Relatorio`, `!Ajuda`, `!Cancelar`; SIM antes de agir; resultado real | `bot.py`, `ops.py` | `test_bot`, e2e |
| 3.4 Segurança do bot | ver seção 5 | `bot.py`, `cdbs.py`, `whatsapp.py` | `test_bot`, `test_whatsapp` |
| 3.5 Integração real | configuração segura + testes simulados; o que falta para o real | `docs/WHATSAPP.md`, `docs/BOT.md` | **PENDENTE** (exige conta na Meta) |
| 3.6 Painel profissional | estados, links, erros recentes, posições com ações, histórico filtrado, mensagens acionáveis, botão travado durante a ação, celular sem rolagem lateral | `web/` | e2e (390 px) |
| 3.7 Sete melhorias | 1 = itens 1.3/2.5 · 2 = 2.1/2.2 · 3 = 1.6 + `doctor` · 4 = 3.1–3.4 · 5 = 1.4 · 6 = 1.2/1.5 · 7 = 2.4/3.6 | — | — |
| 3.8 Três proteções | 1 conflitos = 2.6 · 2 atualização segura = 2.5 · 3 separar controle e publicação = D1, D2 (supervisor fora do servidor) + falhas parciais testadas | — | — |

## 8. Limitações conhecidas (honestas)

- **Nada foi executado num Android real.** Não foram testados:
  - o cloudflared de verdade;
  - a API da Meta de verdade;
  - o comportamento do Android com a tela desligada.

  Os resultados vêm de um contêiner Linux, com programas simulados. A lista do que testar está em [`TERMUX.md`](TERMUX.md).
- Se o **celular** parar (bateria, Android encerrando o Termux, sem internet), **ninguém avisa**: o próprio CDBS é quem estaria fora. Isso exigiria um monitor externo, fora do escopo.
- Quick Tunnel: link muda a cada reinício. Para o bot, recomenda-se um endereço fixo.
- O WhatsApp tem custo por mensagem e exige modelos aprovados pela Meta.
- Os pacotes de atualização não têm assinatura digital (D8).
