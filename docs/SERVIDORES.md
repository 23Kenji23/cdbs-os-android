# Servidores (4.0): programas que rodam junto com os sites

Um **site** do CDBS é estático: o CDBS só entrega os arquivos (HTML, CSS, imagens…). Ele não roda nada.
Um **servidor** é um **programa seu** (Python ou Node.js) que o CDBS **liga, vigia e publica**. Com ele dá para fazer o que um site estático não faz: receber formulários, guardar dados, ter uma API, gerar páginas na hora.

- Até **5 servidores** (posições 1 a 5), ao lado das **20 posições de site**.
- Cada um fica em `https://SEU-LINK/<nome>/`, pelo **mesmo túnel** dos sites. Não é preciso abrir porta nem criar outro túnel.
- O painel do CDBS (porta 8089) **continua fora da internet**. Só a porta pública (8088) passa pelo túnel.

## Como funciona, em uma figura

```
internet → Cloudflare → túnel → CDBS (porta 8088) ─┬─ /adega/…   → arquivos do site (data/sites/adega)
                                                   └─ /recados/… → repassa para 127.0.0.1:8101 (seu programa)
                                                                     ↑ ligado e vigiado pelo guardião (supervisor)
```

O seu programa escuta **só no próprio celular** (`127.0.0.1`). Quem fala com a internet é o CDBS: ele recebe o pedido em `/recados/…`, tira o `/recados` do começo do caminho e repassa para o programa.

## Passo a passo

1. **Node.js (só se for usar):** no Termux, `pkg install nodejs -y`. Python já vem com o CDBS.
2. Painel → **Servidores** → **Criar servidor**:
   - **Nome:** vira o endereço (`/nome/`). Letras minúsculas, números e hífen. Não pode ser o nome de um site que já existe, nem `api`, `admin`, `static` ou `assets`.
   - **Tipo:** Python 3 ou Node.js.
   - **Arquivo de entrada:** o arquivo que liga o programa (ex.: `app.py`, `server.js`).
   - **Caminho de saúde (recomendado):** um endereço do seu programa que responde 200 quando ele está bem (ex.: `/saude`). Sem ele, o guardião só confere se a porta está aberta.
3. Compacte a pasta do programa em **.zip** e envie no cartão do servidor (**Enviar código**).
4. Toque em **Implantar**. O guardião troca o código, liga o programa e espera ele responder a verificação de saúde. Se não responder, **volta sozinho** para o código anterior (se havia um) e registra o motivo no histórico.
5. Abra `https://SEU-LINK/<nome>/` (o link aparece no cartão).

Prontos para testar: [`exemplos/servidor-python/`](exemplos/servidor-python/) (mural de recados que recebe formulário e guarda no celular) e [`exemplos/servidor-node/`](exemplos/servidor-node/) (contador de visitas). Nenhum precisa instalar biblioteca.

## O que o seu programa recebe (variáveis de ambiente)

| Variável | Valor | Para quê |
|---|---|---|
| `PORT` | a porta do servidor (8101 a 8105 por padrão) | **escute nela** |
| `HOST` | `127.0.0.1` | escute só no celular |
| `CDBS_BASE_PATH` | `/<nome>` | montar links absolutos (`{BASE}/recado`) |
| `CDBS_APP_DATA` | `data/apps/<nome>/dados` | **guardar dados aqui**: esta pasta sobrevive a implantações novas |
| `CDBS_APP_NAME` | o nome | — |
| `NODE_ENV` | `production` (se você não definir outro) | — |

Também passam `PATH`, `HOME`, `LANG`, `TMPDIR` e as do Termux. **Nenhuma outra variável do CDBS** (tokens do WhatsApp, senha de e-mail, configurações) chega ao seu programa.

**Variáveis suas** (chaves de API, configuração): cartão do servidor → **Variáveis**. Até 50, nomes `MAIUSCULAS_COM_SUBLINHADO`. Ficam em `data/apps/<nome>/env.json` (permissão 0600) e o painel **não mostra os valores de volta**, só os nomes. Não dá para trocar `PORT`, `HOST`, `PATH` e outras que o CDBS define, nem criar nomes começando com `CDBS_`. Valem a partir do próximo **Reiniciar**.

## Caminhos: o que o programa vê

Com **"Tirar o prefixo"** ligado (padrão): o visitante pede `/recados/api/lista` e o programa recebe `/api/lista`. Por isso o programa pode ser escrito como se estivesse na raiz.

O CDBS ainda:
- corrige redirecionamentos: se o programa responde `Location: /`, o visitante vai para `/recados/`;
- corrige o caminho dos cookies (`Path=/` vira `Path=/recados/`), para um servidor não ver os cookies do outro;
- manda `X-Forwarded-For` (IP do visitante), `X-Forwarded-Proto`, `X-Forwarded-Host` e `X-Forwarded-Prefix` (`/recados`).

Nas páginas HTML, prefira **links relativos** (`href="api/lista"`, sem a barra do começo) ou use `CDBS_BASE_PATH`.

Desligue "Tirar o prefixo" só se o seu programa já espera receber `/recados/…` inteiro.

## O guardião

- **Caiu?** Ele liga de novo, esperando 2 s, 5 s, 15 s, 30 s, 1 min, 2 min e 5 min entre as tentativas.
- **Caiu 6 vezes em 15 minutos?** Para de tentar e o estado fica **"Parado por falhas"**, com aviso (WhatsApp/e-mail, se ligados). Corrija e toque em **Ligar**.
- **Travou** (a verificação de saúde falhou 3 vezes seguidas, uma a cada 15 s)? Ele reinicia.
- O programa e tudo o que ele abriu (grupo de processos) são encerrados juntos: primeiro com SIGTERM, depois à força. Ao iniciar, o guardião também encerra programas que sobraram de uma execução anterior.
- **Registro:** o que o programa escreve na tela (print, console.log) vai para `data/apps/<nome>/logs/saida.log` (até 512 KB, mais 2 arquivos antigos). Veja no cartão → **Registro**.
- No cartão: estado, desde quando está ligado, memória (RSS), CPU, reinícios e o último motivo de parada.

## Ações no painel

| Ação | O que faz | Confirmação |
|---|---|---|
| Implantar | código enviado → no ar, com volta automática se não ficar saudável | — |
| Voltar versão | volta para o código anterior | sim |
| Ligar / Desligar | desligado responde **503** "fora do ar" ao visitante | desligar: sim |
| Reiniciar | para e liga de novo (aplica variáveis novas) | sim |
| Remover | desliga e **move** a pasta para `data/removidos/apps/` (não apaga) | digitar o nome |

## Limites

| | |
|---|---|
| ZIP do código | 200 MB · 800 MB descompactado · 40 000 arquivos (cabe `node_modules`) |
| Pedido do visitante | 25 MB por padrão (ajustável de 1 a 100 MB no cartão) |
| Tempo de resposta | 60 s por padrão (5 a 300 s); passou → **504** |
| Portas | 8101 a 8105 (troque o começo com `CDBS_APP_PORT_BASE` no `.env`) |

O ZIP é conferido antes de qualquer coisa: links simbólicos, caminhos com `..` e arquivos fora da pasta são recusados. Uma pasta única por fora (`meu-app/…`) é reconhecida. `__pycache__`, `.git` e `__MACOSX` são ignorados.

## O que ainda **não** funciona

- **WebSocket** (chat ao vivo, socket.io em modo websocket): o visitante recebe **501**. Use pedidos comuns (fetch) ou consulta periódica.
- **Envio "chunked"** (sem `Content-Length`): **411**. Navegadores e `fetch` com corpo normal mandam o tamanho; funciona.
- **`npm install` pelo painel:** instale as dependências no seu computador ou no Termux e mande a pasta `node_modules` dentro do ZIP. Pacotes com partes compiladas precisam ser instalados **no próprio Termux** (arquitetura ARM).
- **Bancos de dados como serviço** (MySQL, Postgres) não são ligados pelo CDBS. Use SQLite (Python já tem) ou arquivos JSON em `CDBS_APP_DATA`.

## ⚠ Segurança: leia antes de usar

- **O seu programa roda com as mesmas permissões do CDBS** (o mesmo usuário do Termux). **Não existe isolamento** ("sandbox"): um programa pode ler e apagar arquivos do CDBS, inclusive sites, backups e credenciais. **Só implante código que você escreveu ou em que confia.** Não aceite ZIP de servidor de cliente.
- O CDBS **nunca roda comandos digitados** no painel, no bot ou vindos da internet: ele só executa `python3 <arquivo de entrada>` ou `node <arquivo de entrada>`, com o arquivo escolhido na criação, dentro da pasta do código.
- Tudo o que o programa recebe veio da internet. Valide os dados, escape o HTML que mostrar (veja o `html.escape` no exemplo Python) e não monte comandos de terminal com eles.
- O painel pede senha; o programa **não**. Se o seu servidor tiver área restrita, a senha dela é responsabilidade do programa.

## Atualizando da 3.0

Depois de instalar a 4.0 (pelo painel ou pelo `atualizar.sh`), **reinicie o CDBS** uma vez (Sistema → **Reiniciar o servidor** não basta: pare o `bash start.sh` com `Ctrl+C` e inicie de novo). O guardião da 3.0 que já estava rodando não sabe ligar servidores; enquanto ele estiver ativo, a página Servidores avisa e as ações ficam bloqueadas.

## Arquivos

```
data/apps/<nome>/app.json      configuração (tipo, entrada, saúde, limites)
data/apps/<nome>/env.json      variáveis suas (0600)
data/apps/<nome>/atual/        código no ar
data/apps/<nome>/anterior/     código anterior (para Voltar versão)
data/apps/<nome>/dados/        CDBS_APP_DATA — sobrevive a implantações
data/apps/<nome>/logs/         registro do programa
data/system/apps.json          estado de cada servidor (escrito pelo guardião)
data/removidos/apps/           servidores removidos
```

O **backup da Central** cobre as lojas, não os servidores. Para guardar os dados de um servidor, copie `data/apps/<nome>/dados/`.
