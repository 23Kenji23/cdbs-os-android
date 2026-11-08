> **Nota:** este é o guia de uso original do CDBS.OS 4.0, mantido como registro de como o sistema foi pensado. O repositório é um **conceito**, não um produto pronto — veja o [README](../README.md).

# CDBS.OS — sites simples no Android

O CDBS.OS é um painel local para hospedar **sites** (até 20 monitorados) e, desde a 4.0, **servidores** (até 5 programas Python ou Node.js) no Android com Termux, tudo publicado pelo mesmo túnel Cloudflare. Opcionalmente, cada cliente pode ganhar um painel (aberto por um botão escondido no próprio site) para cadastrar os próprios produtos (veja [Catálogo para clientes](#catálogo-para-clientes-o-cliente-cadastra-os-próprios-produtos)). Ele é separado do CDB atual: não usa o banco, a API, a chave nem as pastas do CDB pessoal. Não precisa de banco de dados. A única biblioteca extra é `python-cryptography`, usada só para criptografar os backups (sem ela tudo funciona, menos criar backups novos).

### O que a versão 4.0 trouxe

| Recurso | Para que serve | Guia |
|---|---|---|
| **Servidores (até 5)** | programas seus em Python ou Node.js — formulários, dados, APIs, páginas geradas na hora — ligados e vigiados pelo guardião (reinício com espera crescente, verificação de saúde, registro) e publicados em `/<nome>/` pelo mesmo túnel; implantação com volta automática | [`docs/SERVIDORES.md`](SERVIDORES.md) |
| **20 posições de site** | o dobro da 3.0; quem tinha 10 mantém os mesmos números; relatório e bot já mostram as 20 | [`docs/MONITORAMENTO.md`](MONITORAMENTO.md) |
| **Painel em páginas** | menu lateral (no celular, no topo) com Início, Sites, Servidores, Lojas, Backups, Avisos, Sistema e Histórico, em vez de uma página única comprida; selos no menu mostram o que precisa de atenção; o botão voltar do Android funciona | — |
| **Armazenamento** | na página Início: quanto ocupam sites, servidores, lojas, backups e o próprio CDBS, e quanto sobra no aparelho | — |
| **Limites maiores para sites** | ZIP de até 60 MB (era 15), 250 MB descompactado (era 50) e 3 000 arquivos (era 300) | — |

Como a 4.0 foi organizada: [`docs/ARQUITETURA-4.0.md`](ARQUITETURA-4.0.md). **Depois de atualizar da 3.0, reinicie o CDBS uma vez** (pare o `bash start.sh` e inicie de novo) — o guardião antigo não sabe ligar servidores.

### O que a versão 3.0 trouxe

| Recurso | Para que serve | Guia |
|---|---|---|
| **Guardião (supervisor)** | `bash start.sh` inicia um processo que vigia o servidor: reinicia se cair ou travar (com limite, sem laço), cuida do túnel e instala atualizações | [`docs/TERMUX.md`](TERMUX.md) |
| **20 posições monitoradas** | cada site numa posição fixa, verificado de verdade (pedido HTTP no aparelho e, se quiser, pelo link público), com estados claros: ativo, só no aparelho, fora do ar, inconclusivo, pausado, vago | [`docs/MONITORAMENTO.md`](MONITORAMENTO.md) |
| **Notificações e WhatsApp (API oficial da Meta)** | relatório periódico das 20 posições e alertas (queda, volta, túnel, atualização, backup, espaço), sempre registrados primeiro no histórico local | [`docs/WHATSAPP.md`](WHATSAPP.md) |
| **Avisos por e-mail** | os mesmos avisos pelo seu e-mail (ex.: Gmail com senha de app), sem conta na Meta e sem custo por mensagem | [`docs/EMAIL.md`](EMAIL.md) |
| **Bot `!Link`** | consultar e agir pelo WhatsApp (menu de 8 opções, `!Status`, `!Relatorio`), só para números autorizados e com SIM antes de mudar algo | [`docs/BOT.md`](BOT.md) |
| **Atualização pelo painel** | enviar o ZIP, conferir (SHA-256 de cada arquivo), instalar com cópia de segurança, verificação de saúde e volta automática | [`docs/ATUALIZACOES.md`](ATUALIZACOES.md) |
| **Painel operacional** | visão geral, posições com ações (verificar, pausar/retomar), túnel, reinício do servidor, histórico de eventos com filtros, tudo com confirmação e resultado real | — |
| **Diagnóstico** | `bash diagnostico.sh` (= `python3 cdbs.py doctor`): confere instalação, configuração, permissões, espaço, processos, túnel e WhatsApp, sem mostrar segredos | [`docs/ANDROID.md`](ANDROID.md) |
| **Sites removidos guardados** | remover um site tira do ar, mas guarda os arquivos até você recuperar ou apagar de vez | — |

Como tudo foi organizado e por quê: [`docs/ARQUITETURA-3.0.md`](ARQUITETURA-3.0.md). **Testado num computador Linux** (testes automáticos + navegador simulando celular); **ainda não num Android real** — a lista do que falta testar está no fim de [`docs/TERMUX.md`](TERMUX.md).

## Como adicionar um site

No painel, entre com a senha e envie um arquivo **`.zip`**. O ZIP deve conter um `index.html` na pasta principal. Se o ZIP tiver uma única pasta externa, o CDBS tenta reconhecê-la. O site é publicado como `/<nome>/`; por exemplo, o site `adega` abre em `/adega/`.

O limite é 60 MB por ZIP, até 250 MB depois de extraído e 3 000 arquivos. O CDBS recusa `.env`, arquivos ocultos, links simbólicos e código de servidor como PHP ou Python. Aceita HTML, CSS, JavaScript, imagens, fontes e outros arquivos estáticos. Não executa o que foi enviado como site.

Precisa de algo que rode no servidor (formulário que grava, API, banco)? Isso é um **servidor**, não um site: veja [`docs/SERVIDORES.md`](SERVIDORES.md).

## Instalar ou atualizar no Termux

> **Roteiro completo, passo a passo e copiável: [`docs/TERMUX.md`](TERMUX.md).** Ele inclui o `atualizar.sh` (atualização com cópia de segurança e volta atrás) e o `diagnostico.sh`. O resumo abaixo continua valendo.

Se ainda não fez, permita acesso à pasta Downloads:

```sh
termux-setup-storage
```

Para instalar pela primeira vez, extraia o ZIP em uma pasta nova. **Não extraia por cima do CDB antigo.**

```sh
pkg update -y
pkg install python python-cryptography unzip cloudflared -y
pkg install nodejs -y        # só se for usar servidores em Node.js
mkdir -p ~/cdbs-os
unzip ~/storage/downloads/CDBS-Android-Cloudflare-4.0.zip -d ~/cdbs-os
cd ~/cdbs-os
cp config/.env.example .env
```

Se já tem o CDBS instalado, primeiro pare o servidor antigo com `Ctrl+C`. Depois atualize os arquivos sem apagar seus sites nem sua senha:

```sh
pkg update -y
pkg install python-cryptography cloudflared unzip -y
cd ~/cdbs-os
unzip -o -j ~/storage/downloads/CDBS-Android-Cloudflare-4.0.zip atualizar.sh -d ~/cdbs-os
bash atualizar.sh ~/storage/downloads/CDBS-Android-Cloudflare-4.0.zip
bash diagnostico.sh --offline
```

O ZIP de atualização **não contém** `.env` nem nada de `data/` (senha, sites, catálogos, backups, credenciais do WhatsApp). Portanto, esses dados ficam no aparelho. Se sua pasta do CDBS tiver outro nome, troque `~/cdbs-os` pelo caminho dela. A partir da 3.0, as próximas atualizações podem ser feitas pelo painel (**Sistema → Atualizações**).

## Fazer o teste público pelo Cloudflare

O teste usa um **Quick Tunnel** grátis. Não precisa entrar na conta Cloudflare nem alterar o DNS do domínio comprado. Ele cria um endereço temporário `https://....trycloudflare.com` e publica os sites. O endereço muda quando você reinicia o túnel; ele para de funcionar quando o processo é encerrado e não tem garantia de disponibilidade. É para teste, não para prometer hospedagem 24 horas.

1. No Termux, entre na pasta e inicie o CDBS (o `start.sh` já pede o `termux-wake-lock`):

   ```sh
   cd ~/cdbs-os
   bash start.sh
   ```

   O painel para você administrar continua no próprio celular: `http://127.0.0.1:8089`.

2. No painel, em **Sistema → Túnel Cloudflare**, toque em **Ligar**. (Pelo terminal também dá: em outra sessão, `bash start-cloudflare.sh` — com o CDBS rodando pelo `start.sh`, ele só pede ao guardião para ligar o túnel.)

3. Espere aparecer `🟢 conectado — https://....trycloudflare.com`. Para abrir um site chamado `adega`, use o endereço seguido de `/adega/`, por exemplo: `https://SEU-ENDERECO.trycloudflare.com/adega/`. O link também aparece na visão geral e, se o WhatsApp estiver configurado, chega como "Novo link público".

4. Teste o link em outro celular ou usando outra internet. Para desligar só o túnel: **Sistema → Túnel → Desligar** (os sites continuam no celular). Para parar tudo: `Ctrl+C` na sessão do `start.sh`.

O túnel aponta **somente** para `127.0.0.1:8088`, a porta pública dos sites. O painel administrativo fica em `127.0.0.1:8089` e não é exposto. Se o túnel cair, o guardião tenta religar com espera crescente e limite. Qualquer pessoa que tenha o endereço temporário pode visitar os sites publicados; não envie conteúdo privado.

### Se aparecer erro de DNS

Em algumas combinações de Android e Termux, `cloudflared` pode falhar ao resolver DNS local, por exemplo com `api.trycloudflare.com` ou `[::1]:53`. Se acontecer, pare o processo e envie o texto ou uma captura do erro. **Não faça root, não abra a porta 8089 e não instale correções de fontes desconhecidas.**

## Usar o domínio comprado mais tarde

Este teste **não usa `cdbsos.com.br`**. Depois que o Quick Tunnel funcionar, podemos configurar um Named Tunnel com endereço estável e os registros DNS no Cloudflare. O registro do domínio pode continuar no lugar onde foi comprado, mas o DNS terá de ser apontado para a Cloudflare. O token do túnel é privado: não o envie em mensagens.

## Segurança e funcionamento

O painel e os sites escutam somente em `127.0.0.1`. O CDB antigo continua separado e não deve ser ligado ao túnel. O seu painel administrativo não aparece no endereço público; só o painel dos clientes, com senha própria, em `/_painel/`. Não encaminhe portas no roteador.

O Android pode limitar rede e atividade em segundo plano quando fica parado com a tela desligada. `termux-wake-lock`, deixar o aparelho ligado à energia e remover a otimização de bateria do Termux ajudam, mas não garantem funcionamento 24 horas. Veja [Doze e App Standby do Android](https://developer.android.com/training/monitoring-device-state/doze-standby).

O Cloudflare Quick Tunnel é temporário e tem limites próprios de teste, como ausência de garantia de disponibilidade e mudança do hostname a cada inicialização. Consulte [Quick Tunnels](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/) e [instalação do cloudflared](https://developers.cloudflare.com/tunnel/downloads/). O pacote `cloudflared` pode ser instalado pelo repositório do [Termux](https://github.com/termux/termux-packages/tree/master/packages/cloudflared).

## Catálogo para clientes (o cliente cadastra os próprios produtos)

**O que é.** Um painel simples, com senha, onde cada cliente seu cadastra, edita e apaga os próprios produtos (nome, preço, foto, descrição, categoria e "esgotado"). O site do cliente, que **você** faz em HTML, lê essa lista sozinho.

**Por que existe.** Sem isso, todo produto novo ou preço alterado vira um pedido de alteração para você, um novo ZIP e uma nova publicação. Com o catálogo, o cliente atualiza a vitrine dele na hora e você só cuida do visual e da venda do site. Como o HTML é seu, você decide o que é exclusivo e como cobrar.

**Como funciona, em três peças:**

1. **Seu painel** (`127.0.0.1:8089`, só no seu celular): em "Catálogo dos clientes" você ativa o catálogo de uma loja e recebe a senha do cliente.
2. **Painel do cliente**, dentro do próprio site: o cliente abre o site dele, toca **5 vezes seguidas no logo** e o painel abre por cima, pedindo só a senha. Ele mora no mesmo endereço dos sites, em `/_painel/`, então passa pelo **mesmo túnel**. Não existe segundo endereço para mandar ao cliente.
3. **API pública de leitura**: o seu HTML busca os produtos em `/_api/NOME/products.json` e as fotos em `/_media/NOME/...`.

### Passo a passo

1. Publique o site normalmente (ZIP com `index.html`), por exemplo com o nome `adega`.
2. No seu painel, em **Catálogo dos clientes**, preencha o nome (**o mesmo do site**), o nome da loja e o WhatsApp, e toque em **Ativar catálogo**.
3. Toque em **Copiar mensagem para o cliente** e envie por WhatsApp. A senha aparece uma única vez; se perder, use **Nova senha**.
4. No HTML do site, coloque o botão escondido e leia os produtos (veja abaixo). Um exemplo completo e comentado está em `docs/exemplo-catalogo.html`. É só referência de uso, não um modelo para entregar.
5. Para testar sem outro celular, use o botão **Painel do cliente** ao lado da loja no seu painel.

### Botão escondido (o login do cliente dentro do site)

Duas linhas no HTML:

```html
<!-- 1) o ajudante do botão escondido, antes dos seus scripts -->
<script src="/_cdbs/admin.js" defer></script>

<!-- 2) marque o "ponto secreto" com data-cdbs-admin, de preferência o logo -->
<a class="logo" href="#topo" data-cdbs-admin>...</a>
```

- **5 toques em até 3 segundos** no elemento marcado abrem o painel por cima do site. Um visitante comum não vê nada diferente, e quem achar sem querer só encontra um pedido de senha.
- Também abre pelo endereço do site com `#painel` no final: `https://SEU-ENDERECO/adega/#painel`. É útil como plano B.
- Se a página não marcar nenhum elemento, o ponto secreto é o **canto inferior esquerdo** da tela.
- O botão **voltar** do Android fecha o painel, sem sair do site.
- Ao fechar, se o cliente mudou algo, o site **recarrega sozinho** para mostrar a novidade. Para atualizar sem recarregar (e não perder um carrinho montado), o seu script pode tratar o evento:

  ```js
  window.addEventListener('cdbs:atualizado', (evento) => {
    evento.preventDefault();      // "deixa comigo, não recarregue"
    carregarProdutos();
  });
  ```

- O nome da loja vem do endereço do site (`/adega/` → loja `adega`). Se o catálogo tiver outro nome, use `<script src="/_cdbs/admin.js" data-loja="outro-nome" defer></script>`.
- **Link direto**, sem o site: `https://SEU-ENDERECO/_painel/?loja=adega`. Vai na mensagem para o cliente, caso o HTML ainda não tenha o botão.

### Ler os produtos no HTML

O jeito mais curto, usando o ajudante opcional:

```html
<script src="/_cdbs/catalog.js"></script>
<script>
  CDBS.carregar('adega').then((loja) => {
    for (const produto of loja.products) {
      // produto.name, produto.price_text, produto.image, produto.available...
      const link = CDBS.linkWhatsApp(loja, produto);   // botão "Pedir no WhatsApp"
    }
  });
</script>
```

Sem o ajudante, `fetch('/_api/adega/products.json')` funciona igual. Os dois ajudantes (`catalog.js` e `admin.js`) podem ser usados juntos.

### O que a API devolve

```json
{
  "store": {"slug": "adega", "name": "Adega do Zé", "whatsapp": "5585999999999", "whatsapp_url": "https://wa.me/5585999999999"},
  "categories": ["Cervejas", "Vinhos"],
  "products": [
    {"id": "a1b2c3d4e5f6", "name": "Vinho Tinto", "description": "...", "category": "Vinhos",
     "available": true, "price_cents": 8990, "price_text": "R$ 89,90", "image": "/_media/adega/a1b2...jpg"}
  ],
  "updated": "2026-10-21T23:59:00Z"
}
```

`price_cents` é `null` e `price_text` é `""` quando o cliente deixa o preço em branco (mostre "Consulte"). `image` é `null` sem foto. Produtos esgotados continuam na lista com `available: false`, para o seu HTML decidir como mostrar.

### ⚠ Segurança no seu HTML: nunca use `innerHTML` com texto do produto

Quem digita nome, descrição e categoria é o cliente. Se o seu HTML colocar esse texto na página com `innerHTML`, alguém que invada a conta do cliente (ou o próprio cliente) consegue injetar um script no site. Use sempre `textContent`, como no exemplo. O CDBS entrega o texto cru de propósito, para você escapar do jeito certo para cada layout.

Isso ficou **mais importante** agora que o painel mora no mesmo endereço dos sites: um script injetado em um site rodaria no mesmo endereço do painel. Ele não consegue ler o cookie de sessão (é `HttpOnly` e só vale em `/_painel`), mas poderia agir pelo painel no navegador de quem estiver logado. Com `textContent` em tudo que vem do catálogo, esse risco não existe. Mais tarde, com domínio próprio, dá para dar um subdomínio para cada loja e isolar ainda mais.

### Colocar na internet

Nada muda em relação aos sites: **um túnel só**.

```sh
bash start.sh
# depois: painel → Sistema → Túnel → Ligar (ou, em outra sessão: bash start-cloudflare.sh)
# o painel dos clientes vai junto, em /_painel/
```

O seu painel administrativo (8089) continua **sem túnel**. Com Quick Tunnel o endereço muda a cada reinício, mas o cliente não precisa saber disso: ele entra pelo próprio site. Quando tiver um endereço fixo (Named Tunnel com domínio), preencha `CDBS_PUBLIC_BASE` no `.env` e o link direto passa a sair pronto na mensagem para o cliente.

### O que protege os dados

- O painel do cliente tem senha gerada pelo CDBS (16 caracteres sem letras confusas, guardada só como hash PBKDF2), limite de tentativas por IP e sessão de 7 dias em cookie `HttpOnly`/`SameSite=Strict`, válido só em `/_painel`. A sessão sobrevive a reinício do servidor, e **Nova senha** derruba todos os acessos abertos.
- Alterações só são aceitas se vierem do próprio endereço (confere o cabeçalho `Origin`). O painel só pode ser aberto por cima de páginas do mesmo endereço (`frame-ancestors 'self'`): outro site não consegue embuti-lo.
- Cada cliente só mexe na própria loja: o nome da loja vem do cookie assinado, nunca do pedido.
- Fotos: só JPG, PNG e WebP, identificados pelo conteúdo do arquivo (o nome e o tipo informados são ignorados). SVG e HTML são recusados. As fotos saem com tipo fixo, `nosniff` e política que impede execução de script.
- O celular do cliente reduz a foto antes de enviar (lado maior de até 1200 px), então uma foto de câmera de 6 MB vira algo como 300 KB, e o seu upload do celular aguenta.

### Limites

200 produtos por loja, fotos de até 2 MB (a redução automática já mantém abaixo disso), 60 MB de fotos por loja. O cliente edita **somente produtos**. Dados da loja (nome, WhatsApp) e senha, só você, no seu painel. Não há carrinho nem pagamento: o pedido é feito pelo botão de WhatsApp que você coloca no HTML, então o CDBS não guarda dados pessoais de compradores.

### Backup

Use a **Central de backups** do painel (seção no fim da página). Desde a Evolução 2.1, todo backup novo é **criptografado** com uma **senha de recuperação** que você define uma vez e que **não pode ser recuperada se for perdida**. A Central também faz exclusão manual com confirmação, prévia de retenção (sem exclusão automática), recuperação de loja removida (volta desativada até gerar nova senha) e restauração com aviso de manutenção e reversão. Guias: [`docs/BACKUP.md`](BACKUP.md) (uso) e [`docs/CRIPTOGRAFIA.md`](CRIPTOGRAFIA.md) (senha e formato).

Os backups ficam em `data/backups/` (fora dos sites, nunca públicos) e **nada é apagado automaticamente**. Para proteger também contra perda do aparelho, use **Baixar cópia** e guarde o arquivo `.cdbk` em outro lugar.

Cópia manual completa (inclui a senha do painel e a chave de sessão, que o backup da Central não leva): copie `data/catalogs/`, `data/sites/`, `data/admin.json` e `data/secret.key`. Apagar `data/secret.key` apenas faz todos os clientes entrarem de novo.

## Senha e recuperação

A senha do painel fica somente neste aparelho, guardada como hash PBKDF2; o CDBS não a envia a um serviço externo. Se esquecê-la, pare o servidor, apague apenas `data/admin.json` e inicie de novo. O painel pedirá uma nova senha; os sites ficam em `data/sites/` e não são apagados.

## Pastas

- `cdbs.py`: servidor do painel local e dos sites estáticos (o painel dos clientes fica dentro dele, em `/_painel/`, e o webhook do WhatsApp em `/_whatsapp/webhook`).
- `supervisor.py`: o guardião (inicia e vigia o servidor, cuida do túnel, executa os pedidos do painel/bot e instala atualizações).
- `apps.py`: os servidores (4.0) — configuração, código enviado, implantação com volta, processos vigiados, registro.
- `site_monitor.py`, `ops.py`, `publication.py`: as 20 posições, as ações do painel (verificar, pausar/retomar) e a pausa de publicação.
- `notify.py`, `whatsapp.py`, `emailer.py`, `bot.py`: central de notificações, cliente da API oficial do WhatsApp, envio por e-mail (SMTP) e bot `!Link`.
- `updater.py`, `versioninfo.py`, `VERSION.json`: atualização pelo painel (validação, cópia, instalação, saúde, volta) e o manifesto com o SHA-256 de cada arquivo.
- `settings.py`, `config.py`, `control.py`, `audit.py`, `lifecycle.py`, `sysutil.py`: configurações (painel + `.env`), pedidos ao guardião, histórico de eventos, sinal de vida e utilidades.
- `doctor.py` (+ `diagnostico.sh`): o diagnóstico.
- `tools/`: `montar_pacote.py` (monta o ZIP com o `VERSION.json`) e `cobertura.py` (mede a cobertura dos testes).
- `tests/`: testes automáticos (`python3 -m unittest discover -s tests`); `tests/e2e/` tem os testes de navegador (precisam de Node + Playwright, num computador).
- `catalog.py`: dados do catálogo (lojas, produtos, fotos, senhas e sessões).
- `backup.py`: Central de backups (criar, validar, exportar/importar, restaurar com reversão, recuperar loja, excluir, retenção manual, lembrete).
- `backup_crypto.py`: criptografia dos backups (AES-256-GCM + scrypt; formato em `docs/CRIPTOGRAFIA.md`).
- `atualizar.sh`: atualização reversível no Termux (faz cópia completa antes; `--voltar` desfaz).
- `diagnostico.sh`: confere instalação, biblioteca de criptografia, portas e permissões, sem mostrar segredos.
- `docs/TERMUX.md`: roteiro de instalação, atualização, primeiro backup/restauração e volta atrás.
- `web/`: interface do seu painel (`nav.js` faz as páginas, `apps.js` a página Servidores) e os ajudantes `catalog.js` (ler produtos) e `admin.js` (botão escondido), sem fontes ou scripts externos.
- `client/`: interface do painel dos clientes.
- `docs/exemplo-catalogo.html`: exemplo de como um site lê o catálogo e tem o botão escondido.
- `data/sites/`: arquivos publicados no aparelho.
- `data/catalogs/`: produtos e fotos de cada loja.
- `data/admin.json`: hash da senha do painel; criado no primeiro uso.
- `data/secret.key`: chave que assina as sessões dos clientes; criada no primeiro login de cliente.
- `data/backups/`: backups por loja (`.cdbk` criptografado, ou `.zip` legado, + registro) e o verificador da senha de recuperação; pasta privada (0700), fora dos sites.
- `data/system/`: histórico de eventos (`audit.jsonl`), configurações do painel, credenciais (`secrets.json`, 0600), estado do guardião e do túnel, pedidos ao guardião; privada (0700).
- `data/monitoring/`, `data/notifications/`, `data/updates/`: posições e verificações, notificações, pacotes recebidos e cópias do programa para voltar versão; privadas.
- `data/apps/<nome>/`: cada servidor (código atual e anterior, `dados/` que sobrevive a implantações, variáveis em `env.json` 0600, registro).
- `data/removidos/sites/` e `data/removidos/apps/`: sites e servidores removidos guardados até você recuperar ou apagar de vez; privada, nunca servida.
- `data/admin-sessions.json`: sessões do painel (só o hash), para não precisar entrar de novo depois de um reinício.
- `config/.env.example`: portas e opções para configurar endereços HTTPS estáveis.
- `start.sh`: inicia o CDBS pelo guardião (`bash start.sh --verificar` só confere se dá para iniciar).
- `start-cloudflare.sh`: liga o Quick Tunnel dos sites (com o guardião rodando, pede a ele; sem o guardião, abre direto). O painel dos clientes vai junto.

Os **sites** continuam estáticos: o CDBS não executa nada do ZIP de um site. O que é dinâmico fica em dois lugares: o catálogo de produtos (recurso do próprio CDBS) e os **servidores** (4.0), que são programas seus em Python ou Node.js. ⚠ Um servidor roda com as mesmas permissões do CDBS, **sem isolamento**: implante só código seu ou em que você confia ([`docs/SERVIDORES.md`](SERVIDORES.md#-segurança-leia-antes-de-usar)).


## Plano mestre de atualizações (Partes 1, 2 e 3)

Consulte `docs/updates/PLANO-MESTRE-CDBSOS-PARTES-1-2-3.md` para a sequência obrigatória de implementação. O arquivo consolida testes, segurança, backup/restauração, watchdog, monitoramento das 20 posições, notificações, atualizador com rollback, bot WhatsApp `!Link`, painel, diagnóstico e proteções contra concorrência/falhas. Os documentos detalhados estão em `docs/updates/`. A presença das especificações não comprova que os recursos foram implementados ou testados.

**Situação na versão 3.0:** as três partes foram implementadas, na ordem 1 → 2 → 3. O que cada item do plano virou (arquivo, teste) e as decisões de arquitetura estão em [`docs/ARQUITETURA-3.0.md`](ARQUITETURA-3.0.md); o inventário feito antes de mexer, em [`docs/INVENTARIO.md`](INVENTARIO.md). Os testes rodaram num contêiner Linux, com cloudflared e API da Meta **simulados**. Continuam **pendentes**: teste num Android/Termux real, túnel com o cloudflared de verdade e WhatsApp real (conta, token, modelos aprovados, webhook e entrega confirmada) — veja o fim de [`docs/TERMUX.md`](TERMUX.md).
