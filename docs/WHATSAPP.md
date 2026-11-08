# WhatsApp no CDBS.OS (3.0): relatório, alertas e bot pela API oficial

## O que é e para que serve

O CDBS pode mandar para o **seu** WhatsApp:
- o **relatório das 20 posições** (a cada 30 minutos, por padrão);
- **alertas** quando algo muda: site caiu ou voltou, túnel caiu, link público novo, atualização instalada ou desfeita, backup falhou, pouco espaço.

Ele também pode **receber comandos** seus pelo WhatsApp, o bot `!Link` (veja [`BOT.md`](BOT.md)).

**Não quer criar conta na Meta?** Os mesmos avisos podem ir por **e-mail**: veja [`EMAIL.md`](EMAIL.md). O e-mail funciona sozinho ou junto com o WhatsApp; só o bot `!Link` precisa do WhatsApp.

Tudo isso usa a **API oficial da Meta**, a *WhatsApp Business Platform* (Cloud API).

**Por que a oficial, e não um "robô do WhatsApp Web".** Bibliotecas que automatizam o WhatsApp Web (QR code) violam os termos do WhatsApp. O número pode ser **banido**, e elas quebram a cada mudança do app. O plano proíbe isso como solução de produção. A API oficial é estável e mostra se a mensagem foi entregue. O preço disso é uma configuração inicial na Meta, que é o que esta página ensina.

> **Situação real desta versão.** O código foi testado com uma **API da Meta de mentira** (testes automáticos e teste no navegador): formato das mensagens, assinatura do webhook, modelos, janela de 24 h, falhas e repetição.
> **Nenhuma mensagem de verdade foi enviada** e nenhum modelo foi aprovado, porque isso exige a sua conta na Meta. Por isso o envio real continua **PENDENTE** até você fazer os passos abaixo e confirmar que chegou no seu celular.

## Antes de começar: o que você vai precisar

| Item | Para quê | Onde |
|---|---|---|
| Conta de desenvolvedor da Meta | criar o "app" que fala com a API | developers.facebook.com |
| Portfólio empresarial (Business Manager) | dono do número e dos modelos | business.facebook.com |
| Um **número de envio** | quem manda as mensagens | número de teste da Meta (grátis, só para testes) ou um número seu que **não** esteja no app do WhatsApp |
| Um **link público fixo** (recomendado) | a Meta entrega as respostas e os status no seu webhook | túnel nomeado do Cloudflare com domínio (veja "Endereço do webhook") |

Para **só receber relatório e alertas**, o webhook não é obrigatório: as mensagens saem mesmo sem ele. Mas sem webhook você não vê "entregue/lido", e o bot não funciona.

## Passo a passo

### 1. Criar o app e pegar o número de envio

1. Em **developers.facebook.com → Meus apps → Criar app**, escolha o caso de uso de **WhatsApp** (conectar com clientes pelo WhatsApp) e ligue o app ao seu portfólio empresarial.
2. No menu do app, abra **WhatsApp → Configuração da API** (*API Setup*). Ali aparecem:
   - um **número de teste** da Meta;
   - o **Phone number ID** (identificador do número de envio, só dígitos). **Não** é o número de telefone.
   - um **token temporário**. Ele expira em cerca de 24 h e serve só para o primeiro teste.
3. Ainda nessa tela, em **Para** (*To*), **adicione o seu número pessoal** e confirme o código que chega nele.

   Com o número de teste, a Meta só entrega para números cadastrados aí. Sem isso, o envio falha com o erro **131030** ("número não está na lista permitida"). O CDBS mostra esse erro na lista "Últimas notificações".

### 2. Token permanente (para não parar a cada 24 h)

1. Em **business.facebook.com → Configurações do negócio → Usuários → Usuários do sistema**, crie um usuário do sistema (função **Admin**).
2. Em **Atribuir ativos**, dê a ele o **app** e a **conta do WhatsApp** (controle total).
3. Toque em **Gerar token**:
   - escolha o app;
   - expiração **Nunca**;
   - permissões `whatsapp_business_messaging` e `whatsapp_business_management`.
4. Copie o token. Ele aparece uma vez.

O token é como uma senha: quem tem o token manda mensagens em seu nome. **Não mande por WhatsApp, não cole em print, não coloque em ZIP.** Ele vai direto no painel do CDBS (passo 5).

### 3. Chave secreta do app (App Secret)

Em **developers.facebook.com → seu app → Configurações do app → Básico → Chave secreta do app → Mostrar**.

**Para que serve.** Cada evento que a Meta manda para o seu webhook vem assinado com essa chave (cabeçalho `X-Hub-Signature-256`). O CDBS confere a assinatura **antes de ler** o evento. Sem a chave certa, nada é aceito. É isso que impede alguém que descobriu o seu link de fingir ser a Meta e mandar comandos ao bot.

### 4. Modelos de mensagem (templates)

**Por que precisa.** A Meta só deixa uma empresa mandar texto livre **dentro de 24 h** depois da última mensagem que a pessoa mandou para ela (a "janela de atendimento"). Fora da janela, só vale um **modelo aprovado**. O relatório periódico e os alertas quase sempre saem fora da janela, então precisam de modelo.

O CDBS faz assim:
- **janela aberta**: manda texto livre (o relatório completo, com quebras de linha);
- **janela fechada**: manda o modelo aprovado;
- **sem modelo**: a mensagem fica como "bloqueado" no histórico, com o motivo.

Crie em **WhatsApp Manager → Modelos de mensagem → Criar modelo**:
- **Categoria:** Utilidade (*Utility*);
- **Idioma:** Português (BR), código `pt_BR`.

**Modelo do relatório** — nome `cdbs_relatorio_sites`, 12 variáveis. Desde a 4.0 são **20 posições**, então cada linha leva **duas** posições (ex.: `01 🟢 Ativo — adega  |  02 ⚪ Vago`); o número da posição já vem dentro da variável. Corpo:

```
CDBS.OS - relatório de sites de {{1}}:
{{2}}
{{3}}
{{4}}
{{5}}
{{6}}
{{7}}
{{8}}
{{9}}
{{10}}
{{11}}
Resumo: {{12}}. Detalhes no painel do CDBS.
```

> **Já tinha o modelo da 3.0 aprovado?** Ele continua funcionando (o número de variáveis é o mesmo, 12), sem cadastrar nada de novo. Só fica com a numeração repetida — `1) 01 🟢 Ativo — adega  |  02 ⚪ Vago`. Se isso incomodar, cadastre o corpo acima como um modelo novo e troque o nome no painel.

**Modelo resumido do relatório (alternativa)** — use se a Meta recusar o de 12 variáveis (ela pode recusar modelos com "variáveis demais para o tamanho do texto"). Mesmo nome ou outro, 3 variáveis:

```
CDBS.OS - relatório de sites de {{1}}. Situação: {{2}}. Posições que pedem atenção: {{3}}. Veja os detalhes no painel do CDBS.
```

Com o resumido aprovado, escolha **"3 — resumo e posições com problema"** em **Notificações → Destinatários e preferências → Variáveis do modelo do relatório**. No `.env`, o equivalente é `CDBS_WHATSAPP_REPORT_VARIABLES=3`.

**Modelo dos alertas** — nome `cdbs_alerta_site`, 3 variáveis:

```
CDBS.OS - alerta: {{1}}. Detalhe: {{2}}. Horário: {{3}}. Veja o painel do CDBS para mais informações.
```

**Exemplos de variáveis** (a Meta pede no cadastro):

| Modelo | Exemplo |
|---|---|
| Relatório | `{{1}}` = `26/10/2026 15:30` · `{{2}}` = `01 🟢 Ativo — adega  |  02 ⚪ Vago` · `{{3}}` = `03 ⚪ Vago  |  04 ⚪ Vago` … · `{{12}}` = `1 ativo · 19 vagos` |
| Resumido | `{{2}}` = `1 ativo · 1 fora do ar · 18 vagos` · `{{3}}` = `4) 🔴 Fora do ar — loja` |
| Alerta | `{{1}}` = `Posição 4 (loja): 🔴 Fora do ar` · `{{2}}` = `HTTP 404` · `{{3}}` = `26/10/2026 15:30` |

**Regras da Meta que o CDBS já respeita:**
- o corpo não começa nem termina com variável;
- uma variável **não pode ter** quebra de linha, tab nem 4 espaços seguidos (erro 132018). O CDBS limpa cada variável antes de enviar;
- o número de variáveis enviado tem de ser **igual** ao do modelo aprovado. Por isso existe a escolha 12/3 no painel.

Se você usar outros nomes de modelo, troque no painel (**Modelo do relatório** / **Modelo dos alertas**).

A aprovação costuma levar de minutos a algumas horas. Enquanto não aprovar, o envio fora da janela falha com o erro do modelo, que aparece no histórico.

### 5. Colocar tudo no painel do CDBS

No painel (`http://127.0.0.1:8089`), seção **Notificações e WhatsApp**:

1. **Credenciais da API** (toque para abrir). Os campos são **só de gravação**: depois de salvar, o painel nunca mostra o valor de volta, só "configurado ••••1234". Preencha:
   - **Phone number ID**, do passo 1;
   - **Token de acesso**, do passo 2;
   - **App Secret**, do passo 3.
2. **Token de verificação do webhook**: toque em **Gerar**. Ele aparece **uma vez**. Copie para usar no passo 6.
3. **Destinatários e preferências**:
   - marque **Enviar pelo WhatsApp**;
   - em **Números que recebem**, coloque o seu número no formato internacional (ex.: `+55 85 9xxxx-xxxx`, até 5 números);
   - confira os nomes dos modelos e o número de variáveis;
   - toque em **Salvar preferências**.
4. Toque em **Pré-visualizar relatório** para ver o texto. Depois toque em **Enviar mensagem de teste** e confirme.
5. Em **Últimas notificações**, acompanhe o envio:
   - **aceito pela API**: a Meta recebeu o pedido. **Ainda não é entrega**;
   - **✓✓ entregue** e **✓✓ lido**: só aparecem com o webhook configurado (passo 6);
   - **✕ falhou** ou **⚠ bloqueado**: o motivo aparece ao lado.

> **Onde as credenciais ficam.** Em `data/system/secrets.json`, que só o dono do arquivo lê (permissão 0600). Ou no `.env` (`CDBS_WHATSAPP_ACCESS_TOKEN`, `CDBS_WHATSAPP_APP_SECRET`, `CDBS_WHATSAPP_VERIFY_TOKEN`, `CDBS_WHATSAPP_PHONE_NUMBER_ID`), se preferir: o que estiver no `.env` vence o painel.
>
> Elas **nunca** entram no histórico, nos registros, no diagnóstico, nos backups da Central nem nos pacotes de atualização.

### 6. Endereço do webhook (para ver "entregue/lido" e usar o bot)

1. Ligue o túnel (**Sistema → Túnel Cloudflare → Ligar**). O endereço aparece em **Credenciais da API → Endereço do webhook para a Meta**. Ele tem este formato:

   ```
   https://SEU-LINK/_whatsapp/webhook
   ```

2. Em **developers.facebook.com → seu app → WhatsApp → Configuração**, na parte **Webhook**:
   - **URL de retorno** (*Callback URL*): o endereço acima;
   - **Token de verificação**: o que você gerou no passo 5.2.

   Toque em **Verificar e salvar**. O CDBS responde ao teste da Meta só se o token conferir, e o histórico mostra "webhook verificado".
3. Em **Campos do webhook**, assine **`messages`**. É esse campo que traz as mensagens que você manda para o bot **e** os status de entrega.

**⚠ Quick Tunnel muda de endereço.** O link `https://….trycloudflare.com` **muda toda vez que o túnel reinicia** (reinício do celular, queda da internet longa, "Reiniciar túnel"). Quando muda:
- o CDBS avisa "Novo link público" (pelo WhatsApp, se configurado);
- **você precisa atualizar a URL de retorno na Meta**, senão os status e os comandos do bot param de chegar. O envio de relatórios e alertas continua funcionando, porque não depende do webhook.

Para usar o bot de verdade, o recomendado é um **endereço fixo**:
1. túnel nomeado do Cloudflare com um domínio seu;
2. em **Sistema → Túnel**, modo **Túnel nomeado**;
3. token do túnel em **Credenciais** (ou `CDBS_TUNNEL_TOKEN` no `.env`);
4. `CDBS_PUBLIC_BASE=https://sites.seudominio.com.br` no `.env`.

**O que o webhook expõe.** Só a rota `/_whatsapp/webhook` no servidor **dos sites** (8088), o mesmo que já vai para o túnel. O painel administrativo (8089) continua **sem túnel**. O webhook:
- recusa evento sem assinatura válida (401);
- recusa corpo maior que 512 KB (413);
- limita a 300 pedidos por minuto;
- nunca executa o texto recebido.

## Quanto custa (outubro de 2026)

A Meta cobra **por mensagem entregue** (desde 1º/7/2025). Conferido na página oficial de preços em 26/10/2026:
- **Mensagens de utilidade** (os modelos do relatório e dos alertas) são **cobradas**. Desde **1º/10/2026**, isso vale também dentro da janela de 24 h.
- **Mensagens de serviço** (texto livre dentro da janela, como as respostas do bot) têm **1.000 grátis por mês por número de envio** desde **1º/10/2026**. A partir da 1.001ª, são cobradas.
- No Brasil, a cobrança passou a poder ser em **reais** (contas em BRL, desde julho/2026).

Valores de referência encontrados em sites de parceiros (não são a tabela oficial; confirme a sua no Gerenciador do WhatsApp): mensagem de utilidade no Brasil **≈ R$ 0,035 a R$ 0,05**.

**Conta de cabeça para o relatório.** O relatório de 30 em 30 minutos dá 48 mensagens por dia, ≈ 1.440 por mês.

| Intervalo do relatório | Mensagens por mês | Custo aproximado (R$ 0,035–0,05) |
|---|---|---|
| 30 min (padrão) | ≈ 1.440 | **≈ R$ 50 a R$ 72/mês** |
| 60 min | ≈ 720 | ≈ R$ 25 a R$ 36/mês |
| 120 min | ≈ 360 | ≈ R$ 13 a R$ 18/mês |
| relatório desligado (só alertas) | só quando algo acontece | centavos |

Os alertas saem a mais, mas só quando algo muda, com espera mínima entre repetidos (padrão 60 min).

**Como gastar menos:**
- aumente o intervalo em **Sites → Posições de site → Configurar o monitoramento**;
- ou desligue **Relatório periódico** e deixe só **Alertas**. Desde a 3.0, com o relatório desligado, **as quedas encontradas nas verificações agendadas viram alerta próprio**, e você continua sendo avisado.

Fontes: [preços da Meta](https://developers.facebook.com/documentation/business-messaging/whatsapp/pricing) · [valores no Brasil (Message Central)](https://www.messagecentral.com/pt-br/blog/whatsapp-business-api-pricing-in-brazil) · [valores no Brasil (gwhats)](https://gwhats.com/en/precos-api-whatsapp)

## Quando algo dá errado

| O que aparece | Causa provável | O que fazer |
|---|---|---|
| ⚠ Falta configurar | falta Phone number ID, token ou número de destino | preencher no painel (passo 5) |
| falhou — DESTINATARIO_NAO_PERMITIDO (erro 131030 da Meta) | com número de teste, o destino não está na lista permitida | passo 1.3 |
| falhou — TOKEN_INVALIDO (erro 190) | token expirado (o temporário dura ~24 h) ou revogado | gerar token permanente (passo 2) e salvar de novo |
| falhou — MODELO_INEXISTENTE (132001) ou MODELO_PARAMETROS (132000) | modelo inexistente, ainda não aprovado, nome/idioma diferente ou número de variáveis diferente | conferir nome, `pt_BR` e a escolha 12/3 |
| falhou — MODELO_PARAMETROS (132018) | variável com quebra de linha | não deve acontecer (o CDBS limpa); se aparecer, mande o print |
| falhou — JANELA_FECHADA (131047) | a janela de 24 h fechou entre a decisão e o envio | o CDBS tenta de novo, já com o modelo |
| bloqueado — fora da janela e sem modelo | campo do modelo vazio | preencher o nome do modelo aprovado |
| ◔ pendente por muito tempo | sem internet | o CDBS tenta de novo após 30 s e 2 min, depois marca "falhou" (sem laço infinito) |
| "aceito" e nunca "entregue" | webhook não configurado ou URL antiga (Quick Tunnel mudou) | passo 6 |

O histórico (**Histórico de eventos**, categoria **Notificações**) registra cada envio, com os números mascarados. O diagnóstico (`bash diagnostico.sh`) também confere a configuração, sem mostrar nenhum segredo.

## O que falta testar de verdade (PENDENTE)

1. Criar o app, o token permanente e os dois modelos, e conseguir a **aprovação** da Meta.
2. **Mensagem de teste** chegando no seu celular (fora da janela, por modelo) e um alerta real (ex.: pausar um site de teste e verificar).
3. Webhook verificado pela Meta e status **entregue/lido** aparecendo no painel.
4. Comportamento quando o Quick Tunnel muda de endereço (atualizar a URL na Meta) ou, melhor, com endereço fixo.
5. Custo real na sua fatura depois de alguns dias.
