# Atualizações planejadas — CDBS.OS

## Documento principal e ordem obrigatória

**Leia primeiro:** `PLANO-MESTRE-CDBSOS-PARTES-1-2-3.md`.

Esse arquivo reúne os requisitos em uma única especificação, dividida na ordem obrigatória:

1. **Parte 1 — Base confiável:** inventário, testes em todos os setores, segurança, backup/restauração, auditoria, diagnóstico e Android/Termux.
2. **Parte 2 — Operação confiável:** watchdog, monitoramento das 10 posições, notificações WhatsApp, painel operacional, atualizador seguro, rollback, concorrência e isolamento.
3. **Parte 3 — Controle e interface:** bot WhatsApp `!Link`, comandos básicos, confirmações, segurança, painel profissional e melhorias adicionais.

O plano mestre incorpora, ao final, o conteúdo integral dos documentos detalhados listados abaixo. Eles são mantidos separadamente para facilitar consulta, mas o plano mestre é a referência central para a sequência de implementação.

## Documentos detalhados mantidos no ZIP

- `ATUALIZACAO-CDBSOS-TESTES-BOT-WHATSAPP.md` — testes obrigatórios e bot `!Link`.
- `ATUALIZACAO-CDBSOS-MONITORAMENTO-WHATSAPP.md` — monitoramento das 10 posições e relatório WhatsApp.
- `ATUALIZACAO-CDBS-SISTEMA-NOTIFICACOES.md` — notificações e central local.
- `ATUALIZACAO-CDBSOS-ATUALIZADOR-PELO-PAINEL.md` — atualização pelo painel, proteção de dados e rollback.

## Instrução geral para Claude

- Inspecione o código real antes de implementar.
- Implemente na ordem Parte 1 → Parte 2 → Parte 3.
- Claude está autorizado a alterar e reorganizar código/arquitetura quando necessário, preservando dados e funcionalidades existentes.
- Execute testes reais e reporte apenas resultados confirmados. Não afirme que recursos estão implementados só porque a documentação existe.
- Se faltarem credenciais ou ambiente Android real, implemente testes simulados e identifique explicitamente o teste real pendente.
