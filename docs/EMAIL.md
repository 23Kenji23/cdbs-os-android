# Avisos por e-mail (3.0)

## O que é

Os mesmos avisos que podem ir pelo WhatsApp também podem ir para o **seu e-mail**:
- o relatório das 20 posições;
- os alertas: queda, volta, túnel, link novo, atualização, backup, pouco espaço.

O e-mail é **independente** do WhatsApp. Dá para usar:
- só e-mail;
- só WhatsApp;
- os dois juntos.

**Por que existe.** Não precisa de conta na Meta, de modelo aprovado nem de webhook, e não tem custo por mensagem. Usa uma conta de e-mail que você já tem.

> **Situação real:** testado com um servidor de e-mail **de mentira** (no computador):
> - login com usuário e senha;
> - mensagem com assunto e texto do relatório;
> - senha errada;
> - servidor fora do ar;
> - destino recusado.
>
> **Ainda não foi enviado para um Gmail de verdade (PENDENTE):** faça o passo a passo abaixo e confira se chegou.

## Passo a passo (Gmail)

O Gmail **não aceita a sua senha normal** para programas como o CDBS. Ele exige uma **senha de app**: um código de 16 letras só para o CDBS, que você pode apagar quando quiser sem mexer na senha da conta.

1. Ligue a **verificação em duas etapas** da conta Google: Conta Google → Segurança → Verificação em duas etapas. Sem ela, a opção de senha de app não aparece.
2. Crie a senha de app em `myaccount.google.com/apppasswords`:
   - dê o nome "CDBS";
   - copie o código de 16 letras. Ele aparece em 4 grupos (`abcd efgh ijkl mnop`) e pode ser colado com ou sem os espaços.
3. No painel do CDBS, vá em **Notificações e WhatsApp → E-mail** (toque para abrir) e preencha:
   - **Provedor:** Gmail (o servidor `smtp.gmail.com`, a porta 465 e a segurança SSL são preenchidos sozinhos);
   - **Seu e-mail:** o endereço do Gmail;
   - **Senha de app:** o código do passo 2. É só gravação: depois de salvar, o painel nunca mostra de volta;
   - **E-mails que recebem:** se deixar vazio, manda para você mesmo;
   - marque **Enviar avisos por e-mail**.
4. Toque em **Salvar e-mail** e depois em **Enviar e-mail de teste**. Confira a caixa de entrada e também o **spam** na primeira vez.

Em **Últimas notificações**, o envio aparece como **"✓ enviado"**. Isso quer dizer que o servidor do Gmail **aceitou** a mensagem; o e-mail não confirma a entrega na caixa como o WhatsApp faz.

## Outro provedor de e-mail

Escolha **Outro** e preencha os dados SMTP do seu provedor:
- **servidor**;
- **porta**: normalmente 465 para SSL ou 587 para STARTTLS;
- **segurança**;
- **usuário** e **senha**. Muitos provedores também exigem uma "senha de app".

O CDBS **sempre** usa conexão criptografada, com o certificado conferido. Sem criptografia, só aceita enviar para o próprio aparelho (127.0.0.1), o que serve para testes.

## Pelo `.env` (opcional)

O que estiver no `.env` vence o painel e aparece travado no painel:

```
CDBS_EMAIL_ENABLED=true
CDBS_SMTP_HOST=smtp.gmail.com
CDBS_SMTP_PORT=465
CDBS_SMTP_SECURITY=ssl
CDBS_SMTP_USER=voce@gmail.com
CDBS_SMTP_PASSWORD=abcdefghijklmnop
CDBS_EMAIL_TO=voce@gmail.com
```

## Quando algo dá errado

| O que aparece | Causa | O que fazer |
|---|---|---|
| falhou — LOGIN_RECUSADO | senha normal no lugar da senha de app, ou senha de app apagada | criar outra senha de app (passo 2) e salvar |
| bloqueado — falta … | servidor, e-mail ou senha não preenchidos | preencher no painel |
| falhou — DESTINO_RECUSADO | endereço de destino não existe | conferir o e-mail que recebe |
| falhou — TLS | porta ou tipo de segurança trocados | 465 + SSL ou 587 + STARTTLS |
| ◔ pendente, depois falhou | sem internet ou servidor fora | o CDBS tenta de novo após 30 s e 2 min e depois desiste: não fica repetindo para sempre |

**Onde fica a senha.** Em `data/system/secrets.json`, que só o dono do arquivo lê (0600), ou no `.env`. Ela **nunca** vai para:
- o histórico;
- as mensagens de erro;
- o diagnóstico;
- os backups;
- os pacotes de atualização.

Os endereços aparecem mascarados no painel e no histórico (`vo•••@gmail.com`).

## Quanto custa

Nada por mensagem. Ainda assim, o relatório a cada 30 minutos dá 48 e-mails por dia. Se for muito:
- aumente o intervalo em **Sites → Posições de site → Configurar o monitoramento**;
- ou desligue o **Relatório periódico** e fique só com os alertas.

O Gmail gratuito tem limite diário de envios, bem acima disso.
