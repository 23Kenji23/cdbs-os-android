# Bot de comandos (WhatsApp)

- Recebe mensagens pelo webhook da Meta (`/_whatsapp/webhook`), verificando a assinatura (`CDBS_WHATSAPP_APP_SECRET`).
- Só atende números autorizados: administradores (`CDBS_BOT_ADMINS`) e leitores (`CDBS_BOT_VIEWERS`).
- Comandos de uma lista fixa (estado, link, ligar/desligar túnel...). Ação que muda algo pede **confirmação** antes.
- Mensagem repetida pela Meta não executa duas vezes (idempotência).
- Limite de uso por número.
- Evolução planejada: adicionar, atualizar e bloquear sites pelo próprio WhatsApp.
