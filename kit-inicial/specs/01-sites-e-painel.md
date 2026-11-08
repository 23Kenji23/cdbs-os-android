# Sites e painel

- O servidor de sites escuta na porta `CDBS_SITE_PORT` (padrão 8088) e serve `data/sites/<nome>/`.
- O painel de administração escuta em **127.0.0.1** na porta `CDBS_ADMIN_PORT` (padrão 8089). É outro servidor, em outra porta.
- Só a porta dos sites vai para o túnel.
- Existem **10 posições fixas** para sites. Cada posição guarda um site ou está vaga.
- Nome do site: só letras minúsculas, números e hífen. Rejeitar `..`, barras e nomes vazios.
- Publicar site = enviar um `.zip`. Validar antes de extrair: tamanho máximo, sem caminhos fora da pasta (zip-slip), tem `index.html`.
- Remover site = mover para `data/removidos/`, nunca apagar direto.
- Painel em HTML, CSS e JS puros, sem framework, pensado para tela de celular.
