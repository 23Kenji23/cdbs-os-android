# Backup criptografado

- Backup dos dados do usuário em arquivo único, **criptografado** com chave derivada de uma senha (biblioteca `cryptography`).
- Verificação de integridade ao criar e ao restaurar. Arquivo corrompido ou senha errada dá erro claro, sem apagar nada.
- Restaurar sempre faz antes uma cópia do estado atual.
- Sem a biblioteca de criptografia, backups novos ficam bloqueados (não gerar backup sem criptografia em silêncio).
- Listar, baixar, restaurar e apagar backups pelo painel.
