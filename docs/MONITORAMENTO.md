# Monitoramento das 20 posições (4.0)

## O que é

O CDBS acompanha até **20 sites** em **20 posições numeradas** e confere, de tempos em tempos, se cada um **está mesmo abrindo**. O resultado aparece em três lugares:
- no painel, na página **Sites → Posições de site**;
- no relatório pelo WhatsApp, se configurado;
- no bot (`!Link 1`, `!Status`).

**Por que "posições".** Com números fixos, "a posição 4 caiu" é sempre o mesmo site, no painel, no relatório e no WhatsApp. Remover um site **libera a posição dele**, mas **não renumera** as outras.

**Por que conferir de verdade.** Ter "uma pasta com o site" ou "um processo rodando" não prova que o visitante consegue abrir a página. O CDBS faz um **pedido HTTP de verdade**, como um navegador faria. Primeiro dentro do aparelho; depois, se ligado, pelo link público.

## Como os sites entram nas posições

- **Primeira vez que a 3.0 inicia:** os sites que já estavam publicados ocupam as posições 1, 2, 3… em ordem alfabética.
- **Vindo da 3.0 (10 posições):** na 4.0 o mapa ganha as posições 11 a 20. **Ninguém muda de número.** Os sites que estavam "fora das 10 posições" entram nas vagas, em ordem alfabética, e o histórico registra "mapa ampliado para 20 posições".
- **Publicou um site novo pelo painel:** ele vai para a primeira posição vaga, e o CDBS já faz uma verificação.
- **Removeu um site:** a posição fica **vaga** ("posição liberada" no histórico).
- **Mais de 20 sites publicados:** os excedentes continuam no ar, mas **sem monitoramento**. O painel avisa: "Fora das 20 posições: …". Para incluir um deles, libere uma posição.
- **Mudar à mão:** em cada posição ocupada há **Liberar posição**. O site continua publicado, só sai do monitoramento. Numa posição vaga aparece **Colocar aqui**, com a lista dos sites que estão fora.

O mapa fica em `data/monitoring/sites.json`. Um site nunca ocupa duas posições.

Os **servidores** (programas Python/Node, [`SERVIDORES.md`](SERVIDORES.md)) têm as **próprias 5 posições** e são vigiados pelo guardião: não entram nestas 20.

## Estados

| Estado | Quer dizer | O que fazer |
|---|---|---|
| 🟢 **Ativo** | abriu no aparelho e, se o teste público estiver ligado, também pelo link público | nada |
| 🟠 **Só no aparelho** | abre no celular, mas o link público **falhou** (ex.: HTTP 530/502 do Cloudflare). O problema é o **túnel**, não o site | Sistema → Túnel: ver o estado, reiniciar |
| 🔴 **Fora do ar** | não abriu no aparelho: página não encontrada (404), erro, página vazia, servidor não responde ou pasta do site sumiu | o motivo aparece embaixo; publicar de novo ou restaurar backup |
| 🟡 **Verificação inconclusiva** | não deu para concluir: demorou demais, sem internet, DNS, redirecionamento, 503 de manutenção ou restauração em andamento | esperar a próxima verificação; se repetir, ver internet e túnel |
| ⏸ **Pausado** | você pausou de propósito | **Retomar** quando quiser |
| ⚪ **Vago** | posição sem site | — |

**"Inconclusivo" não é "fora do ar".** Sem internet, o CDBS não sabe se o link público funciona, então ele não acusa o site, e o relatório não grita alarme falso.

Cada verificação:
- tem prazo curto: 5 s no aparelho, 10 s pelo link público;
- tenta 2 vezes antes de concluir;
- não trava as outras, porque uma posição com problema não impede as demais.

As rodadas **nunca se sobrepõem**: se você pedir "verificar agora" durante uma rodada, o pedido entra na fila e roda logo depois.

## Quando verifica

| Momento | Envia relatório? |
|---|---|
| Ao iniciar o CDBS | não. Só uma verificação local, para o painel já começar com estados reais |
| **No horário do relatório**, alinhado ao relógio: com 30 min (padrão), às :00 e às :30 | **sim**, se o WhatsApp e o relatório estiverem ligados. Cada horário é enviado **no máximo uma vez**, mesmo que o CDBS reinicie |
| Ao publicar, pausar ou retomar um site | não. Só a verificação daquela posição |
| **Verificar** (uma posição) ou **Verificar todas agora**, no painel | não. Se algo mudou de estado, sai como **alerta** |
| `!Link 3`, `!Link 8` ou `!Relatorio` no bot | responde na conversa |

## Teste pelo link público (opcional)

Desligado por padrão. Ligue em **Sites → Posições de site → Configurar o monitoramento → Testar também pelo link público**.

Com ele ligado, o CDBS abre `https://SEU-LINK/NOME/` (o link do túnel ou o `CDBS_PUBLIC_BASE`). É o que mostra se **quem está fora** consegue abrir.

Sem link público, porque o túnel está desligado, a posição fica 🟢 Ativo com a observação **"link público não testado"**.

Observação honesta: o pedido sai do próprio celular, passa pela internet e volta pelo túnel. Ele prova que o caminho **Cloudflare → túnel → celular** funciona. Não prova a rede de quem está do outro lado.

## O relatório

Exemplo do texto (dentro da janela de 24 h do WhatsApp vai assim; fora dela vai pelo modelo aprovado, com o mesmo conteúdo em uma linha por posição):

```
CDBS.OS — RELATÓRIO DE SITES
Verificado: 26/10/2026 15:30

1. 🟢 Ativo — adega
2. 🔴 Fora do ar — loja (HTTP 404 no aparelho (página não encontrada))
3. ⚪ Vago
…
10. ⚪ Vago

Resumo: 1 ativo · 1 fora do ar · 8 vagos
Túnel: conectado
Verificação pública: desligada (teste só no aparelho)
Próxima verificação: ~16:00
```

Se algo mudou desde a última verificação, o relatório começa com **"CDBS.OS — mudança detectada"** e a lista do que mudou. Assim você não recebe duas mensagens.

Para ver antes de configurar o WhatsApp: **Notificações → Pré-visualizar relatório**.

## Alertas de queda e de volta

- Mudou para 🔴 ou 🟠 numa verificação manual: sai um **alerta** na hora.
- Numa verificação agendada, a mudança vai **dentro do relatório**. Se o relatório periódico estiver **desligado**, ela sai como **alerta próprio**. Antes da correção feita nesta versão, essa queda ficaria sem aviso.
- O mesmo problema repetido só é avisado de novo depois da **espera entre alertas** (padrão 60 min). Isso evita enxurrada de mensagens.
- **"Voltou ao ar"** só é avisado se a queda tinha sido avisada.

## Configurar

No painel: **Sites → Posições de site → Configurar o monitoramento**:
- **Verificar e mandar o relatório**: escolha na lista. O padrão é **a cada 30 minutos**; troque por 15 min, 1 h, 2 h, 3 h, 6 h, 12 h ou uma vez por dia, ou escolha **desligado** (as posições só são verificadas quando você tocar em "Verificar");
- **teste pelo link público**.

Toque em **Salvar monitoramento**: vale a partir da próxima rodada. Para só não **receber** o relatório no WhatsApp e continuar verificando, desmarque **Relatório periódico** em Notificações (os alertas de queda continuam).

Ou no `.env`. O que está no `.env` vence o painel e aparece travado com "Definido no .env":

```
CDBS_MONITOR_ENABLED=true
CDBS_MONITOR_INTERVAL_MINUTES=30
CDBS_MONITOR_PUBLIC_CHECK=false
```

**Custo:** cada relatório pelo WhatsApp é uma mensagem cobrada pela Meta. Veja a tabela em [`WHATSAPP.md`](WHATSAPP.md#quanto-custa-outubro-de-2026). Intervalo maior, ou só alertas, gasta menos.

## Onde ficam os dados

| Arquivo | Conteúdo |
|---|---|
| `data/monitoring/sites.json` | o mapa das 20 posições |
| `data/monitoring/last_report.json` | a última verificação |
| `data/monitoring/history.jsonl` | as últimas 300 rodadas (o painel mostra pela API `/api/monitor/history`) |

O histórico de eventos (categoria **Monitoramento**) registra:
- cada **Verificar agora**;
- as posições atribuídas e liberadas;
- os sites excedentes.

## Limites (o que o monitor não faz)

- Se o **celular** estiver desligado, sem bateria ou com o Termux encerrado pelo Android, **não há quem verifique nem quem avise**. Seria preciso um monitor externo (outro aparelho ou serviço), fora do escopo desta versão.
- Ele confere a página inicial de cada site (ou o caminho configurado para a posição), não todas as páginas.
- O tempo de resposta de cada verificação fica registrado no histórico das rodadas, mas não é medição de desempenho.
