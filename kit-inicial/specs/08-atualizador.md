# Atualizador seguro

- Atualização por `.zip`. `VERSION.json` lista cada arquivo com **SHA-256** e tamanho.
- Antes de instalar: validar o pacote (hashes batem, sem caminhos fora da pasta, não toca em `data/` nem em `.env`).
- Instalar: cópia de segurança dos arquivos atuais, troca, **checagem de saúde**.
- Se a checagem falhar: **volta automática** para a versão anterior.
- Dados do usuário, credenciais e configurações nunca são sobrescritos.
- Disponível pelo painel e por `atualizar.sh`.
