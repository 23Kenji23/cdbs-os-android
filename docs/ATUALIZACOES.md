# Atualizar o CDBS.OS (3.0 e 4.0): pelo painel, com cópia de segurança e volta automática

## As duas formas, e quando usar cada uma

| Forma | Quando usar | Precisa |
|---|---|---|
| **Pelo painel** (Sistema → Atualizações) | o jeito normal a partir da 3.0 | o CDBS iniciado com `bash start.sh` (o supervisor) |
| **Pelo Termux** (`bash atualizar.sh pacote.zip`) | vindo da **2.1 ou anterior** (que não têm atualizador no painel), ou se o painel não abrir | o CDBS **parado** |

As duas fazem o mesmo trabalho:
- conferem o pacote **antes** de mexer em qualquer coisa;
- guardam uma cópia do que está instalado;
- trocam só os arquivos do **programa**.

Elas **nunca** tocam no `.env` nem em `data/`, onde estão:
- sites e catálogos;
- senhas;
- backups;
- credenciais do WhatsApp;
- histórico.

## Pelo painel (3.0 em diante)

1. Baixe o ZIP da versão nova no celular.
2. No painel, **Sistema → Atualizações → Pacote de atualização** → escolha o ZIP → **Enviar e conferir**.
3. O CDBS **confere** o pacote e mostra cada item com ✓ ou ✕:
   - **formato:** os arquivos na raiz do ZIP, ou dentro de **uma** pasta (ex.: `cdbs_package/`);
   - **caminhos:** recusa `..`, caminhos absolutos, links simbólicos e nomes repetidos;
   - **segredos e dados:** recusa `.env`, `admin.json`, `secret.key`, `secrets.json`, chaves `.pem/.key` e qualquer coisa em `data/` (só aceita `data/sites/.keep`);
   - **tamanho:** até 25 MB compactado, 80 MB extraído, 800 arquivos;
   - **arquivos obrigatórios:** `cdbs.py`, `supervisor.py`, `updater.py`, o painel etc.;
   - **integridade:** o `VERSION.json` do pacote traz o **SHA-256 de cada arquivo**, e todos têm de bater. Um arquivo trocado ou corrompido no download é recusado;
   - **sintaxe do Python:** cada `.py` é **compilado, não executado**;
   - **formato dos dados:** a versão nova tem de entender os dados atuais;
   - **versão:** tem de ser mais nova. Igual ou mais antiga só com uma confirmação extra.

   Nada disso executa qualquer coisa do pacote.
4. Se passou, toque em **Instalar**, digite **INSTALAR** e confirme. O painel acompanha cada passo:

   | Passo | O que acontece |
   |---|---|
   | Cópia de segurança | cópia de **todos os arquivos do programa atual**, conferida por SHA-256, em `data/updates/versions/` |
   | Parando o servidor | os sites e o painel ficam fora por alguns segundos |
   | Copiando os arquivos novos | arquivo por arquivo, cada troca atômica. Arquivos do programa antigo que não existem mais na versão nova são retirados |
   | Iniciando a versão nova | — |
   | Conferindo a saúde | o painel responde com a **versão nova**, o servidor dos sites responde e o supervisor novo passa no autoteste |
   | ✓ Concluída | ou, se qualquer conferência falhar, **↩ Revertida**: a versão anterior volta sozinha e é conferida do mesmo jeito |

5. O painel fica fora por alguns segundos e volta sozinho. **Não precisa entrar de novo**: a sessão continua.

**Voltar para uma versão anterior:** **Versões guardadas → Voltar para esta** → digite **VOLTAR**. É o mesmo processo, com cópia, troca, verificação e volta automática se falhar. O CDBS guarda as **2 cópias mais recentes**.

**Se o celular desligar no meio:** o progresso fica num diário (`data/updates/journal.json`). No próximo `bash start.sh`, **antes** de iniciar o servidor, o supervisor faz o seguinte:
- se a troca não tinha começado, nada mudou;
- se tinha começado, ele **recoloca a versão anterior** e registra o que houve.

**Por que é o supervisor que instala.** O servidor não pode se substituir enquanto roda. O supervisor é outro processo: ele para o servidor, troca os arquivos, inicia o novo e confere. Se a versão nova não subir, ele ainda está lá para voltar a anterior.

**Uma limitação honesta.** Se a atualização trouxer um `supervisor.py` novo, ele é conferido (autoteste), mas o supervisor **em execução** continua sendo o antigo até o próximo `bash start.sh`. O servidor novo já roda na hora.

**3.0 → 4.0: reinicie uma vez.** Na 4.0 é o supervisor que liga os **servidores** (programas Python/Node). Depois de instalar a 4.0 pela 3.0, os sites, as 20 posições e o painel novo já funcionam, mas a página Servidores avisa que falta reiniciar e não deixa ligar nada. Pare o `bash start.sh` (Ctrl+C) e inicie de novo.

## Pelo Termux (`atualizar.sh`)

Use para sair da **2.1 → 3.0**, porque a 2.1 não tem atualizador no painel. Use também se o painel não abrir.

```sh
# 1) pare o CDBS: Ctrl+C na sessão do start.sh (e do túnel, se estiver em outra sessão)
cd ~/cdbs-os
# 2) pegue o atualizar.sh NOVO de dentro do ZIP (importante: veja o aviso logo abaixo)
unzip -o -j ~/storage/downloads/CDBS-Android-Cloudflare-4.0.zip atualizar.sh -d ~/cdbs-os
# 3) atualize
bash atualizar.sh ~/storage/downloads/CDBS-Android-Cloudflare-4.0.zip
# 4) confira e inicie
bash diagnostico.sh --offline
bash start.sh
```

> **⚠ Use o `atualizar.sh` novo (o passo 2 acima).** Testei rodar o `atualizar.sh` **da 2.1** com o ZIP da 3.0. Ele copia todos os arquivos certinho: os 87 arquivos conferem com o `VERSION.json`, os dados e o `.env` ficam intactos e a cópia completa é feita. **Mas no fim ele para com uma mensagem de erro do bash**, por exemplo `syntax error near unexpected token` ou `…: command not found`. Isso acontece porque ele troca o próprio arquivo enquanto ainda está rodando. Por isso ele **não mostra** o comando de voltar atrás. O novo foi corrigido para isso e tem teste automático.
>
> **Se você já rodou o da 2.1 e viu esse erro**, nada se perdeu:
> 1. confira com `bash diagnostico.sh --offline`: a linha "integridade" deve dar OK e a versão, 3.0.0;
> 2. a cópia completa está em `~/cdbs-os-copia-AAAAMMDD-HHMMSS` (veja com `ls -d ~/cdbs-os-copia-*`);
> 3. para voltar: `bash ~/cdbs-os/atualizar.sh --voltar ~/cdbs-os-copia-AAAAMMDD-HHMMSS`.

O `atualizar.sh` faz, nesta ordem, e para no primeiro erro:
1. confere que o CDBS está parado: servidor **e** supervisor;
2. confere o ZIP: as mesmas regras de formato, caminhos, segredos e dados do painel, mais o SHA-256 do `VERSION.json` quando existir;
3. extrai numa pasta temporária e confere a sintaxe do Python;
4. confere o espaço e faz uma **cópia completa** da pasta, com programa, sites, catálogos, backups e senha (`~/cdbs-os-copia-AAAAMMDD-HHMMSS`). Só então troca os arquivos do programa;
5. mostra o comando para voltar:

```sh
bash ~/cdbs-os/atualizar.sh --voltar ~/cdbs-os-copia-AAAAMMDD-HHMMSS
```

Quando tiver certeza de que está tudo bem (alguns dias), apague a cópia completa para liberar espaço: `rm -rf ~/cdbs-os-copia-AAAAMMDD-HHMMSS`. O CDBS **nunca** apaga essa cópia sozinho.

**Sem painel e com o CDBS parado**, também dá para voltar para a cópia guardada pelo atualizador do painel:

```sh
python3 updater.py voltar            # a cópia mais recente
python3 updater.py verificar pacote.zip   # só confere um pacote, sem instalar
```

## Para quem monta os pacotes

```sh
python3 tools/montar_pacote.py 3.0.1 saida/CDBS-Android-Cloudflare-3.0.1.zip --data 2026-11-01
```

O script:
- gera o `VERSION.json`, com o SHA-256 de cada arquivo do programa;
- monta o ZIP com os arquivos **na raiz**, o formato que o `atualizar.sh` da 2.1 também aceita;
- **nunca** inclui `.env`, `data/` (só `data/sites/.keep`), `__pycache__` nem `*.pyc`;
- recusa montar se aparecer um arquivo com nome de segredo.

Os pacotes não têm assinatura digital. A garantia é o SHA-256 de cada arquivo, que pega arquivo corrompido ou trocado no caminho. Por isso, **instale só pacotes que você mesmo baixou de onde confia**: quem consegue trocar o ZIP inteiro também consegue trocar o `VERSION.json`.

## O que fica registrado

**Histórico de eventos**, categoria **Atualizações**:
- pacote recebido (aprovado ou recusado, com o motivo);
- atualização iniciada, concluída, revertida ou falhou.

Com o WhatsApp configurado, "Atualização instalada" e "Atualização falhou e a versão anterior foi recolocada" também chegam como alerta, um por atualização.
