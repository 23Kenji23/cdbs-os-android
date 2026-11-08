# Instalar, atualizar e testar no Termux (versão 4.0)

Roteiro para fazer **no seu celular**, na ordem. Cada bloco cinza pode ser copiado e colado inteiro no Termux.
Onde estiver `~/cdbs-os`, troque pelo nome da sua pasta se for outro.

> Esta versão foi testada **num computador Linux**: testes automáticos e o painel num navegador Chromium simulando a tela de um celular.
> O supervisor, o túnel e o WhatsApp foram testados com programas **simulados** no lugar do cloudflared e da API da Meta.
> **Ainda não foi rodada num Android/Termux de verdade.** Os passos marcados com 🔶 são os testes que só você pode fazer.

## O que mudou na 4.0, em uma frase cada

- **Servidores:** até 5 programas seus (Python ou Node.js) rodando junto com os sites, vigiados pelo guardião e publicados em `/<nome>/` pelo mesmo túnel. Guia: [`SERVIDORES.md`](SERVIDORES.md).
- **20 posições de site** (eram 10). Quem já tinha sites mantém os mesmos números.
- **Painel em páginas:** Início, Sites, Servidores, Lojas, Backups, Avisos, Sistema e Histórico. No celular, o menu fica no topo e rola para o lado.
- **Armazenamento** visível na página Início; sites aceitam ZIP de até 60 MB.
- **Vindo da 3.0:** depois de atualizar, pare o `bash start.sh` (Ctrl+C) e inicie de novo, uma vez. Só assim o guardião novo entra no ar.

## O que mudou na 3.0, em uma frase cada

- **`bash start.sh` agora inicia o "guardião" (supervisor).** Ele inicia o servidor e o reinicia sozinho se cair ou travar, com limite de tentativas para não entrar em laço. Ele também cuida do túnel e instala atualizações.
- **Uma sessão do Termux basta.** O túnel liga pelo painel (**Sistema → Túnel → Ligar**), e o link aparece no painel. O `bash start-cloudflare.sh` continua funcionando: com o supervisor rodando, ele pede ao supervisor para ligar o túnel.
- **20 posições monitoradas**, com relatório e alertas opcionais pelo WhatsApp (API oficial) e bot `!Link`.
- **Atualização pelo painel**, com cópia de segurança e volta automática.
- **Remover um site não apaga mais os arquivos.** Eles ficam em "Sites removidos" até você recuperar ou apagar de vez.
- **Diagnóstico completo:** `bash diagnostico.sh`, que é o mesmo que `python3 cdbs.py doctor`.

As portas **não mudaram**:
- sites em **8088**: é a porta que vai para o túnel;
- o seu painel em **8089**: **nunca** vai para o túnel.

## Passo 1: preparar o Termux (uma vez)

```sh
termux-setup-storage
pkg update -y
pkg install python python-cryptography unzip cloudflared -y
pkg install nodejs -y      # opcional: só para servidores em Node.js
python3 -c "import cryptography; print(cryptography.__version__)"
```

A última linha deve mostrar um número de versão (ex.: `43.0.3`). Se aparecer `ModuleNotFoundError`, rode `pkg install python-cryptography -y` de novo.

Opcional: o app **Termux:API** (da mesma loja do Termux) e `pkg install termux-api -y`. Com eles, o diagnóstico mostra a bateria e se o celular está na tomada. Veja [`ANDROID.md`](ANDROID.md).

## Passo 2A: primeira instalação (não tinha CDBS)

```sh
mkdir -p ~/cdbs-os
unzip ~/storage/downloads/CDBS-Android-Cloudflare-4.0.zip -d ~/cdbs-os
cd ~/cdbs-os
cp config/.env.example .env
```

## Passo 2B: atualizar quem já tem o CDBS

**Tem a 3.0?** O caminho mais simples é o painel: **Sistema → Atualizações → enviar o ZIP da 4.0 → digitar INSTALAR**. Quando terminar, **pare o `bash start.sh` com Ctrl+C e inicie de novo** (só esta vez). O atualizador da 3.0 troca os arquivos e reinicia o servidor do painel, mas o guardião continua sendo o da 3.0 até você reiniciá-lo — e é o guardião novo que liga os servidores. Até lá, a página Servidores mostra um aviso.

**Tem a 2.1 ou anterior** (ou prefere o Termux)? Siga abaixo.

1. Pare o CDBS: **Ctrl+C** na sessão do `start.sh` e na do túnel, se estiver em outra.
2. Pegue o `atualizar.sh` novo de dentro do ZIP e rode:

```sh
cd ~/cdbs-os
unzip -o -j ~/storage/downloads/CDBS-Android-Cloudflare-4.0.zip atualizar.sh -d ~/cdbs-os
bash atualizar.sh ~/storage/downloads/CDBS-Android-Cloudflare-4.0.zip
```

O script:
- recusa rodar com o CDBS ligado;
- confere o ZIP: caminhos, segredos, dados, SHA-256 de cada arquivo e sintaxe;
- faz uma **cópia completa** da pasta (`~/cdbs-os-copia-AAAAMMDD-HHMMSS`), com sites, catálogos, backups e senha;
- só então troca os arquivos do programa;
- no fim, mostra o comando exato para voltar atrás.

Ele **nunca** mexe em `.env` nem em `data/`. Seus sites, lojas, produtos, backups e a senha do painel continuam os mesmos.

> **Importante:** extraia o `atualizar.sh` novo antes (a linha do `unzip -o -j` acima). O da 2.1 copia tudo certo, mas termina com um erro do bash ("syntax error" ou "command not found") e não mostra o comando de voltar atrás. Se isso já aconteceu, veja [`ATUALIZACOES.md`](ATUALIZACOES.md): nada se perdeu.

Detalhes: [`ATUALIZACOES.md`](ATUALIZACOES.md).

## Passo 3: conferir antes de ligar

```sh
cd ~/cdbs-os
bash diagnostico.sh --offline
```

Cada linha sai com um destes status:
- **OK**: tudo certo;
- **AVISO**: funciona, mas merece atenção. Leia a sugestão;
- **FALHA**: precisa corrigir antes;
- **INFO**: só informação.

Com o CDBS parado, é normal aparecer:
- "supervisor não está rodando";
- "nada respondendo em 8088/8089".

O diagnóstico **não mostra senhas, tokens nem chaves**: pode mandar o resultado quando pedir ajuda.

Sem o `--offline`, ele também testa a internet e o DNS do Cloudflare. Com `--corrigir`, ele aplica **só** correções seguras e diz exatamente o que fez:
- criar pastas que faltam;
- fechar permissões;
- renomear (nunca apagar) um arquivo de configuração corrompido.

## Passo 4: ligar

```sh
cd ~/cdbs-os
bash start.sh
```

Deve aparecer `[guardião] supervisor iniciado` e, logo depois, `[guardião] servidor no ar (painel 127.0.0.1:8089, sites 127.0.0.1:8088)`.

Abra o painel **no próprio celular**: `http://127.0.0.1:8089`.

**Link público (para abrir de outro celular).** No painel, vá em **Sistema → Túnel Cloudflare → Ligar**. Em alguns segundos aparece `🟢 conectado — https://….trycloudflare.com`. Para abrir o site `adega`, use `https://…trycloudflare.com/adega/`.

Se preferir pelo terminal, abra outra sessão e use `bash start-cloudflare.sh` ou `python3 supervisor.py tunnel start`.

Para ligar o túnel sempre junto com o CDBS, marque **Ligar o túnel junto com o CDBS** em **Sistema → Túnel**.

**Parar tudo:** **Ctrl+C** na sessão do `start.sh`. O guardião desliga o túnel e o servidor de forma limpa.

**Comandos úteis** (em outra sessão, dentro da pasta):

```sh
python3 supervisor.py status          # supervisor, servidor e túnel agora
python3 supervisor.py restart         # reinicia o servidor do CDBS (os sites ficam fora alguns segundos)
python3 supervisor.py tunnel restart  # reinicia o túnel (no Quick Tunnel o link MUDA)
python3 supervisor.py stop            # para tudo (o mesmo que Ctrl+C no start.sh)
```

> Iniciar com `python3 cdbs.py` (sem o `start.sh`) ainda funciona, mas **sem** o guardião. Ninguém reinicia o servidor se ele cair, e o painel mostra "Supervisor ausente": túnel, reiniciar e atualizar pelo painel ficam indisponíveis.

## Passo 5: senha de recuperação dos backups (uma vez; quem veio da 2.1 já fez)

1. No painel, role até **Central de backups**. Vai aparecer **"Proteção dos backups: defina a senha de recuperação"**.
2. Toque em **Gerar senha forte** (ou digite uma sua, com 12 ou mais caracteres) e repita a senha.
3. **Guarde a senha agora**, em dois lugares fora deste celular: um gerenciador de senhas e um papel. Veja [`CRIPTOGRAFIA.md`](CRIPTOGRAFIA.md).
4. Marque a confirmação de que guardou e toque em **Ativar proteção**.

⚠ **Se perder essa senha, os backups protegidos não abrem nunca mais.** Ninguém consegue recuperar.

## Passo 6: 🔶 conferir o painel novo no celular (≈10 minutos)

1. Na página **Início** (Visão geral):
   - o cartão **Sistema** mostra `Versão 4.0.0` e "supervisor ativo";
   - o cartão **Posições** (página Início) mostra os seus sites.
2. Na página **Sites → Posições de site**:
   - cada site publicado aparece numa posição;
   - toque em **Verificar** num deles: o estado deve ficar **🟢 Ativo**.
3. **Pausar** um site de teste:
   - confirme;
   - abra o site no navegador: deve aparecer "temporariamente fora do ar";
   - toque em **Retomar**: ele volta.
4. Ligue o túnel e abra o site **de outro celular, com outra internet** (dados móveis).
5. Em **Sistema**, toque em **Reiniciar servidor**:
   - o painel some por alguns segundos e volta sozinho;
   - você continua logado.
6. Na página **Histórico**, filtre por **Sites**: aparecem publicação, pausa e retomada.
7. 🔶 **Servidores** (≈10 minutos): compacte a pasta `docs/exemplos/servidor-python` em ZIP (no Termux: `pkg install zip -y`, depois `cd ~/cdbs-os/docs/exemplos && zip -r ~/storage/downloads/recados.zip servidor-python`). Na página **Servidores**: Criar → nome `recados`, tipo Python, entrada `app.py`, saúde `/saude` → Enviar código → **Implantar**. O estado deve ficar **🟢 Rodando**. Abra `https://SEU-LINK/recados/`, mande um recado e recarregue: ele continua lá. Depois toque em **Reiniciar** e confira que o recado não sumiu.

Se algo der diferente do descrito, rode `bash diagnostico.sh` e mande o resultado com um print da tela.

## Passo 7: primeiro teste de backup e restauração (≈10 minutos)

Use uma loja de teste ou a da Adega fora do horário de movimento.

1. Na loja, toque em **Criar backup**, digite a senha de recuperação e espere **CONCLUÍDO** com "CRIPTOGRAFADO".
2. Toque em **Verificar integridade** → **Verificar tudo**, com a senha. Deve dar "confere".
3. Toque em **Baixar cópia** e guarde o `.cdbk` fora do celular (Drive, computador).
4. Mude algo pequeno no site ou num produto.
5. Em **Histórico e restauração** → **Restaurar…**:
   - leia o resumo;
   - digite a senha e o nome da loja;
   - toque em **Restaurar agora**.
6. Abra o site: a mudança do item 4 deve ter sumido. Na lista deve aparecer um backup **"Segurança (antes de restaurar)"**: ele permite desfazer.

## Passo 8 (opcional): avisos por e-mail, WhatsApp e bot

O mais simples é o **e-mail**, sem conta na Meta: veja [`EMAIL.md`](EMAIL.md) (Gmail com senha de app).

Para receber o relatório das 20 posições e os alertas no seu WhatsApp, e para comandar pelo `!Link`, siga:
1. [`WHATSAPP.md`](WHATSAPP.md): conta na Meta, token, modelos e webhook. Inclui quanto custa;
2. [`BOT.md`](BOT.md).

Sem isso, tudo continua funcionando: os eventos ficam no histórico do painel.

## Voltar atrás (rollback)

| Você atualizou por… | Para voltar |
|---|---|
| `atualizar.sh` (ex.: 2.1 → 3.0) | `bash ~/cdbs-os/atualizar.sh --voltar ~/cdbs-os-copia-AAAAMMDD-HHMMSS` (o comando exato saiu no fim da atualização) |
| painel (3.0 → mais nova) | **Sistema → Versões guardadas → Voltar para esta** (digite VOLTAR); sem painel: `python3 updater.py voltar` com o CDBS parado |

O `--voltar` guarda a versão atual, sem apagar, em `~/cdbs-os-desfeita-…`, e põe a cópia no lugar.

**Atenção:** o que foi criado **depois** da atualização (backups, produtos novos, configurações do WhatsApp) fica na pasta "desfeita", não na restaurada.

Quando tiver certeza de que está tudo bem (alguns dias de uso), apague a cópia completa para liberar espaço:

```sh
rm -rf ~/cdbs-os-copia-AAAAMMDD-HHMMSS
```

## 🔶 O que ainda depende de teste no aparelho (PENDENTE até você testar)

| Item | Por que só no aparelho |
|---|---|
| `pkg install python-cryptography` instala e importa no seu Termux | depende da versão do Termux/Android |
| `bash start.sh` → supervisor → servidor no ar (Passo 4) | caminhos, permissões e processos reais do Android |
| Túnel pelo painel com o **cloudflared de verdade**: o link aparece? O estado vira "conectado"? | o supervisor lê o link da saída do cloudflared e o estado do endereço `/ready` dele; foi testado com um cloudflared simulado |
| Servidor reiniciado sozinho depois de cair (ex.: `kill` no processo do `cdbs.py`) | comportamento de processos no Termux |
| 4.0: servidor Python e Node.js ligados pelo guardião, reiniciados depois de `kill`, e nenhum processo sobrando depois de parar o CDBS | grupos de processos e `/proc` no Android; o Node do `pkg` precisa estar no PATH |
| 4.0: atualização 3.0 → 4.0 pelo painel e depois reinício do `start.sh` | o guardião antigo precisa ser trocado; testado só num computador Linux |
| 4.0: cartão Armazenamento mostrando o espaço livre real | o Android pode informar o espaço do armazenamento de forma diferente |
| Atualização pelo painel e `atualizar.sh` 2.1 → 3.0 no seu Termux | espaço, permissões e velocidade reais |
| Tela desligada, economia de bateria, Wi-Fi caindo, Termux fechado à força, celular reiniciado | o Android pode pausar ou encerrar o Termux (veja [`ANDROID.md`](ANDROID.md)) |
| WhatsApp real: modelo aprovado, mensagem chegando, webhook verificado, bot respondendo | exige a sua conta na Meta ([`WHATSAPP.md`](WHATSAPP.md)) |
| Criar/verificar/restaurar backup criptografado no celular (Passo 7) | velocidade e memória do scrypt (~32 MiB) no seu aparelho |
| Baixar o `.cdbk` e importá-lo de volta pelo navegador do celular | comportamento de download do navegador do Android |
