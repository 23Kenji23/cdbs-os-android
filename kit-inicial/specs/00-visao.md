# Visão

**CDBS.OS** transforma um celular Android (via Termux) num mini-servidor de hospedagem. Ele hospeda sites estáticos e pequenos servidores (Python/Node), publica na internet por túnel Cloudflare **sem abrir portas no roteador** e **sem expor o painel de administração**.

Em uma frase: um painel local que hospeda sites, vigia tudo sozinho e avisa pelo WhatsApp ou e-mail quando algo cai.

## Princípios
- Segurança primeiro: o painel nunca sai de `127.0.0.1`; segredos nunca vão para logs nem para o git.
- Tudo é registrado primeiro num histórico local; WhatsApp e e-mail são só canais.
- Nada de ação destrutiva sem confirmação e sem cópia de segurança.
- Honestidade: o sistema diz o que sabe e o que não sabe (estado "inconclusivo" existe de propósito).

## Fora de escopo
- Garantia de funcionamento 24 horas (o Android pode suspender processos).
- Hospedagem para terceiros em escala.
