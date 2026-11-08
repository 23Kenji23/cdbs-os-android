# Criptografia dos backups (Evolução 2.1)

## Para que serve

A partir da 2.1, todo backup novo é gravado **criptografado** (arquivo `.cdbk`). Quem pegar o arquivo, seja copiado do celular, baixado por engano ou encontrado num pendrive, **não consegue ler** os sites, produtos e fotos sem a **senha de recuperação**. Essa senha é definida por você uma única vez, na Central de backups.

## A senha de recuperação: leia antes de definir

* **O CDBS não grava a senha.** Ele guarda só um *verificador* (`data/backups/protection.json`), um valor calculado a partir dela que serve para conferir se você digitou certo. Com esse valor ninguém descobre a senha.
* **Não existe recuperação.** Se você perder a senha, **todos os backups protegidos ficam ilegíveis para sempre**, inclusive para quem fez o programa. Não há porta dos fundos.
* A mesma senha vale para **todos** os backups deste CDBS. Esta versão **não troca** a senha (trocar exigiria recriptografar tudo; ficou fora do escopo).
* Mínimo de 12 caracteres, com pelo menos 4 símbolos diferentes. O botão **Gerar senha forte** cria uma senha aleatória de 20 caracteres em 4 grupos (ex.: `abcde-fghij-kmnop-qrstu`, sem letras confusas como 0/O e 1/l).
* 5 senhas erradas em 5 minutos travam novas tentativas por 1 minuto.

### Como guardar a senha (faça pelo menos duas)

1. **Gerenciador de senhas** (Bitwarden, o do Google, o da Samsung etc.). Crie um item "CDBS: senha de recuperação dos backups" e cole a senha.
2. **Papel**: escreva a senha à mão e guarde junto com documentos importantes, **longe do celular**.
3. Se outra pessoa de confiança da CLK puder precisar restaurar, deixe uma cópia lacrada com ela.

**Não** guarde a senha num arquivo dentro da pasta do CDBS, nem num print na galeria do mesmo celular, nem numa conversa de WhatsApp. Se o celular for perdido, a senha iria junto com os backups.

## O formato (para conferência técnica)

Nenhum algoritmo foi inventado. As peças são padrões reconhecidos:

| Peça | Escolha | De onde vem |
|---|---|---|
| Cifra autenticada | **AES-256-GCM** (cifra e detecta qualquer alteração) | pacote `cryptography` |
| Chave a partir da senha | **scrypt** N=2^15, r=8, p=1 (~32 MiB de memória), salt aleatório de 16 bytes por arquivo | `hashlib.scrypt` da biblioteca padrão |
| Aleatoriedade | `secrets` / `os.urandom` | biblioteca padrão |
| Blocos | 1 MiB por bloco (construção STREAM), para não precisar do arquivo inteiro na memória | enquadramento do CDBS |

Arquivo `CDBK` versão 1:

```
cabeçalho (32 bytes)
  0..5    "CDBSBK"            identificação
  6       versão (1)
  7       KDF (1 = scrypt)
  8,9,10  log2(N), r, p do scrypt
  11      log2(tamanho do bloco) = 20
  12..27  salt (16 bytes)
  28..31  prefixo do nonce (4 bytes)
blocos: texto cifrado + etiqueta GCM de 16 bytes
  nonce = prefixo(4) + número do bloco(8)
  dados autenticados = cabeçalho + número do bloco + marcador "último bloco"
```

Os dados autenticados prendem cada bloco ao cabeçalho, à posição e ao fim do arquivo. Por isso trocar blocos de lugar, duplicar, cortar o final ou anexar lixo faz a abertura **falhar**, em vez de devolver dados errados. O que está dentro é o mesmo ZIP com inventário SHA-256 da Evolução 2. Depois de abrir, o CDBS ainda confere arquivo por arquivo.

Regras seguidas: a chave **nunca** vai dentro do backup, **não há chave fixa** no código, e senhas, chaves e material criptográfico **não aparecem** em logs, registros ou mensagens de erro. Ao ler um arquivo, os parâmetros do scrypt só são aceitos dentro de limites seguros (N de 2^10 a 2^18, até 256 MiB de memória), para que um arquivo hostil não trave o celular.

## O que acontece sem a biblioteca

O pacote `cryptography` é a **primeira dependência externa** do CDBS. No Termux:

```sh
pkg install python-cryptography -y
python3 -c "import cryptography; print(cryptography.__version__)"
```

Se ele faltar, a Central **não finge proteção**: **bloqueia a criação de backups novos** com a mensagem do comando acima. Listar, verificar sem senha e baixar continuam funcionando. Restaurar backups legados (`.zip`) também, mas nesse caso o backup de segurança feito antes da troca sai **sem criptografia**, porque não há como cifrar.

## Backups antigos (legados, sem criptografia)

Backups feitos na Evolução 2 continuam como estavam (`.zip`) e aparecem marcados **SEM CRIPTOGRAFIA**. O botão **Proteger (criptografar)** cria uma **cópia nova protegida** (tipo "Protegido (cópia de um legado)") e **mantém o original**. Depois de conferir que a cópia protegida está **CONCLUÍDO**, você pode excluir o legado com **Excluir…**. O CDBS nunca apaga o legado sozinho.

## Exportar e importar

* **Baixar cópia** de um backup protegido entrega o próprio `.cdbk`. Ele continua protegido fora do celular.
* **Importar** um `.cdbk` pede a senha. Se o arquivo tiver sido protegido com **outra** senha (de outro CDBS), a importação é recusada com "Este arquivo foi protegido com outra senha".
