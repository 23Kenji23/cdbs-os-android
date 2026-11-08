# Notificações

- Todo evento é gravado **primeiro** no histórico local (`data/notifications/`). Canais são só entrega.
- Canal WhatsApp: API oficial da Meta (Cloud API), com modelos de mensagem. Variáveis `CDBS_WHATSAPP_*`.
- Canal e-mail: SMTP (`CDBS_SMTP_*`, `CDBS_EMAIL_TO`).
- Falha de um canal não perde o evento e não derruba o sistema; registra e tenta de novo.
- Relatório periódico do estado do servidor e alertas imediatos para situações que exigem atenção, com a ação sugerida.
- Telefone e e-mail aparecem mascarados no painel e nos logs.
