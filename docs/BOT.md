# Bot de comandos pelo WhatsApp (3.0): `!Link`, `!Status`, `!Relatorio`

## O que é e para que serve

Com o bot, você consulta e resolve o básico **pelo WhatsApp**, sem abrir o painel no celular do CDBS. Ele serve para saber:
- se os sites estão no ar;
- qual é o link público agora;
- se o túnel caiu.

E para mandar:
- verificar um site;
- retomar um site pausado;
- religar o túnel.

O bot só responde a números que **você** autorizou, e só faz o que está numa **lista fixa de ações**. Tudo que muda algo pede **SIM** antes.

> **Situação real.** Testado com eventos de WhatsApp simulados, assinados como a Meta assina, nos testes automáticos e no teste de navegador.
> Nesse teste, o `!Link 6` com **SIM** fez o supervisor ligar o túnel de verdade (com um cloudflared simulado), e o resultado voltou pela conversa.
> **Ainda não foi usado com o WhatsApp real** (PENDENTE): depende do app da Meta e do webhook ([`WHATSAPP.md`](WHATSAPP.md), passos 1 a 6).

## Ligar

1. Faça a configuração do WhatsApp **com o webhook** ([`WHATSAPP.md`](WHATSAPP.md)). Sem webhook, as suas mensagens não chegam ao CDBS.
2. No painel: **Notificações e WhatsApp → Bot de comandos**.
   - Marque **Aceitar comandos pelo WhatsApp**.
   - **Números que podem CONSULTAR e AGIR**: o seu (formato internacional, ex.: `+55 85 9xxxx-xxxx`).
   - **Números que só podem CONSULTAR** (opcional): ex.: um sócio que só acompanha.
   - **Usar listas e botões**: deixe marcado. Se a lista falhar, o menu vai em texto numerado.
3. Toque em **Salvar bot**. A linha de status deve mostrar **"✓ Bot pronto para receber comandos."** Se faltar algo, ela diz o quê: chave do app, token de verificação, número autorizado…
4. Do seu WhatsApp, mande **`!Ajuda`** para o número de envio do CDBS.

## Comandos

| Comando | O que faz |
|---|---|
| `!Link` | abre o **menu** (lista do WhatsApp, ou texto numerado) |
| `!Link 1` … `!Link 8` | atalho direto para uma opção do menu |
| `!Status` | resumo: versão, desde quando o servidor está no ar, túnel e link, resumo das 20 posições e as que têm problema |
| `!Relatorio` | verifica **agora** todas as posições e manda o relatório detalhado (igual à opção 8) |
| `!Ajuda` | lista de comandos |
| `!Cancelar` | cancela o menu ou a confirmação pendente. Nada é feito |

Maiúsculas e acentos não importam: `!relatório`, `!RELATORIO` e `!Relatorio` são o mesmo comando.

## Menu `!Link`

| Opção | Faz | Quem pode |
|---|---|---|
| 1. Listar as posições | estado atual de cada posição (🟢 Ativo, 🟠 só no aparelho, 🔴 fora do ar, 🟡 inconclusivo, ⏸ pausado, ⚪ vago) | todos os autorizados |
| 2. Links públicos ativos | os endereços que estão respondendo agora | todos |
| 3. Verificar um site | pergunta a posição (1 a 10) ou o nome do site, faz uma verificação **nova** e responde com o resultado real | todos |
| 4. Iniciar/recuperar site | veja abaixo | só **administrador** |
| 5. Estado do túnel | conectado, desconectado ou desligado, mais o link | todos |
| 6. Iniciar/reiniciar túnel | pede **SIM** e pede ao supervisor para ligar o túnel (ou reiniciar, se já estiver ligado). Avisa o resultado real | só **administrador** |
| 7. Posições vagas | quais das 10 estão livres | todos |
| 8. Relatório completo | verifica tudo agora e manda o relatório | todos |

**Opção 4, "iniciar/recuperar".** O bot decide pelo estado **verificado**, não por suposição:

| Situação | O que o bot faz |
|---|---|
| site **pausado** | pede SIM, retoma, verifica de novo e responde "voltou ao ar" só se a verificação der 🟢 Ativo |
| o servidor dos sites **não responde** | oferece reiniciar o servidor pelo supervisor (com SIM) |
| **arquivos do site faltando** | explica que é preciso restaurar um backup **pelo painel**. A senha de recuperação nunca passa pelo WhatsApp |
| já está **ativo** | diz que não há nada a fazer |
| problema só no link público | sugere as opções 5 e 6 (túnel) |

## Exemplo de conversa

```
Você:  !Link
CDBS:  [lista] CDBS.OS — Links · Ver opções
Você:  (escolhe) 6. Iniciar/reiniciar túnel
CDBS:  Você pediu para ligar o túnel (Cloudflare).
       Confirmar? Responda SIM ou CANCELAR (vale por 2 min).   [SIM] [CANCELAR]
Você:  SIM
CDBS:  Pedido enviado: ligar o túnel. Aguarde o resultado (eu aviso aqui).
CDBS:  Resultado (ligar o túnel): ✓ Túnel conectado. Link: https://xxxx.trycloudflare.com
```

## Segurança: o que protege o bot

- **Só números autorizados.** Número desconhecido **não recebe resposta nenhuma**: nem "não autorizado", para não confirmar que o número é um robô. A tentativa fica no histórico com o número mascarado (`+55•••••••1111`).
- **Só eventos da Meta.** Cada evento chega assinado com a chave do app (`X-Hub-Signature-256`). Sem assinatura válida, é recusado **antes de ser lido**.
- **Mensagem para outro número do mesmo app** (ex.: o número de teste) é ignorada.
- **Cada mensagem vale uma vez.** O identificador fica guardado em disco. Se a Meta reenviar, ou se o servidor reiniciar, a mensagem não é executada de novo.
- **Mensagem atrasada não executa.** Se chegou mais de **10 minutos** depois de enviada (ex.: o celular do CDBS estava sem internet), o bot responde pedindo para mandar de novo.
- **Limite de comandos:** 12 por minuto e 60 por hora por número.
- **Menu e confirmação expiram:** o menu em 5 minutos, a confirmação em 2. Responder um número depois disso não faz nada. Só valem as opções que foram **mostradas**: quem só consulta não consegue usar a 6 digitando "6".
- **Botão de confirmação com código.** O botão SIM leva um código único daquela confirmação. Um SIM de uma confirmação antiga não vale.
- **Texto nunca vira comando do sistema.** Cada opção chama uma função fixa do CDBS. Mandar `!Link 1; rm -rf /` só recebe "Opção inválida". O teste automático confere que nenhum pedido ao supervisor nasce de texto livre.
- **"Pronto" só com resultado real.** O bot só diz que deu certo depois do resultado que o supervisor ou a verificação devolveram. Se o resultado não vier em 10 minutos, ele avisa que não teve resposta. Ele nunca presume.
- **O pedido sobrevive a reinício.** Se a ação reinicia o servidor, quem avisa o resultado é o servidor novo, quando volta.

**O bot nunca:**
- recebe site pelo WhatsApp;
- restaura backup;
- mostra senha, token ou chave;
- apaga qualquer coisa;
- instala atualização.

Essas ações ficam **só no painel**, no celular do CDBS.

## Quanto custa

As respostas do bot são **mensagens de serviço**: texto livre dentro da janela de 24 h aberta pela sua mensagem. Desde 1º/10/2026 há **1.000 grátis por mês** por número de envio ([`WHATSAPP.md`](WHATSAPP.md), "Quanto custa"). O uso normal do bot fica longe disso.

## Histórico

No painel: **Histórico de eventos → Categoria: Bot WhatsApp**. Lá aparecem:
- os comandos recebidos;
- as recusas: número não autorizado, atrasada, limite, sem permissão;
- as ações e os seus resultados.

Os números aparecem sempre mascarados.

## Falta testar de verdade (PENDENTE)

1. Webhook verificado pela Meta com o seu link.
2. `!Ajuda`, `!Status` e `!Link` vindos do seu WhatsApp. A lista interativa aparece? E o menu em texto, com a opção desmarcada?
3. `!Link 6` + SIM com o cloudflared real.
4. Mensagem de um número **não** autorizado: não deve chegar resposta nenhuma.
