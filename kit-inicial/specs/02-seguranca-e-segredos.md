# Segurança e segredos

- Login do painel: senha guardada com hash e sal, sessão com validade, limite de tentativas por intervalo.
- Segredos (tokens, senhas, chave de criptografia) ficam em `data/` com permissão restrita (somente o dono) ou em variáveis `CDBS_*`.
- **Mascaramento:** logs, relatórios e mensagens nunca mostram segredo inteiro. Token vira `••••1234`; telefone vira `+55•••••••8888`; e-mail vira `jo•••@gmail.com`.
- Entrada externa nunca vira comando de shell. Cada ação administrativa é uma operação explícita de uma lista fixa.
- O `.env.example` só tem valores fictícios e avisa para nunca preencher e compartilhar.
