# Servidores de usuário (Python/Node)

- Além de sites estáticos, o usuário implanta pequenos servidores enviando um `.zip`.
- Cada servidor roda numa porta própria a partir de `CDBS_APP_PORT_BASE` e fica em `https://SEU-LINK/<nome>/`, pelo mesmo túnel.
- Contrato de saúde: o servidor responde em `/saude`. Implantação só vira definitiva se a saúde responder.
- Operações em lista fixa: iniciar, parar, reiniciar, implantar, **voltar versão**, remover.
- Python usa o `python3` do sistema; Node usa `CDBS_NODE_BIN`.
- Cada servidor é isolado: se cair, não derruba o painel nem os outros.
