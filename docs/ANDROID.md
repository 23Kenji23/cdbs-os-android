# Android e Termux: o que o CDBS precisa e o que o Android pode fazer com ele (Parte 1.6)

O CDBS roda **dentro do seu celular**, então ele só funciona enquanto o celular, o Termux e a internet estiverem funcionando. Esta página explica:
- o que instalar;
- o que o Android costuma fazer com apps em segundo plano;
- o que ajuda;
- o que o CDBS consegue perceber e avisar;
- o que ele **não** consegue.

## Dependências

```sh
pkg update -y
pkg install python python-cryptography unzip cloudflared -y
termux-setup-storage          # uma vez: dá acesso à pasta Downloads (de onde você pega os ZIPs)
```

Opcionais:

| Pacote ou app | Para quê |
|---|---|
| App **Termux:API** (F-Droid) + `pkg install termux-api` | o diagnóstico mostra a bateria e se o celular está na tomada |
| `pkg install nodejs` | só para **servidores** em Node.js ([`SERVIDORES.md`](SERVIDORES.md)); servidores em Python não precisam de nada |
| App **Termux:Boot** (F-Droid) | iniciar o CDBS sozinho depois que o celular reinicia (veja abaixo) |

> Instale o Termux e os apps auxiliares **da mesma loja** (F-Droid ou GitHub). Misturar com a versão da Play Store dá erro de assinatura.

## Espaço e memória com 20 sites e 5 servidores

São duas coisas diferentes:
- **Armazenamento** (os "GB" do celular): sites, códigos dos servidores, fotos e backups. A página **Início → Armazenamento** mostra quanto cada parte ocupa e quanto sobra. Com dezenas de GB livres, os limites do CDBS (60 MB por site, 200 MB por servidor) não chegam perto.
- **Memória RAM**: cada servidor ligado é um programa a mais. Um programa pequeno em Python costuma usar dezenas de MB; em Node.js, um pouco mais. O cartão de cada servidor mostra a memória real (RSS). Se o Android ficar sem RAM, ele encerra o Termux inteiro — por isso **desligue os servidores que não está usando**.

## O que o Android faz com o Termux

- Com a **tela desligada**, o Android entra em economia (Doze) e pode **pausar a rede e os relógios** dos apps em segundo plano.
- Em pouca memória, ou por "otimização" do fabricante (Xiaomi, Samsung, Motorola…), o Android **encerra** o Termux sem aviso.
- Ao **reiniciar** o celular, nada do Termux volta sozinho, a menos que você use o Termux:Boot.
- Sem internet, o túnel cai e o link público para de abrir. O CDBS continua funcionando dentro do celular.

## O que ajuda (mas não garante)

1. `termux-wake-lock`: o `start.sh` já pede isso. Aparece uma notificação "wake lock held".
2. **Tirar o Termux da economia de bateria**: Configurações → Apps → Termux → Bateria → **Sem restrições**. O nome muda conforme o fabricante: "Não otimizar", "Atividade em segundo plano" etc.
3. **Deixar na tomada**. Sem bateria, tudo para, e o CDBS não consegue avisar que parou.
4. Em aparelhos Xiaomi/Redmi: ativar "Início automático" para o Termux e "trancar" o app na tela de recentes.
5. Wi-Fi estável. Dados móveis também servem, mas o Android pode trocar de rede e o túnel reconecta.

Mesmo com tudo isso, **não há garantia de 24 horas**. É um celular, não um servidor.

## O que o CDBS percebe e avisa

| Situação | O que acontece |
|---|---|
| O processo do CDBS foi encerrado à força (Android, falta de bateria, reinício) | No próximo início, o CDBS registra no histórico **"encerramento inesperado"** com a hora do **último sinal de vida** (gravado a cada minuto). O `doctor` mostra isso. Com o WhatsApp configurado, o aviso vai junto quando ele voltar |
| O servidor travou ou caiu mas o Termux continua vivo | O supervisor (3.0) percebe e reinicia, com espera crescente e limite de tentativas (sem loop infinito) |
| Não consegue iniciar (porta ocupada, pasta sem permissão, Python antigo) | `start.sh` e `cdbs.py` explicam o motivo e o que fazer. Fica registrado como "início falhou" |
| Internet caiu | O monitoramento marca o teste público como **inconclusivo**, não como "fora do ar". O túnel é religado quando a rede volta |
| O celular está desligado ou sem internet | **O CDBS não consegue avisar ninguém**, porque é ele mesmo que estaria fora. Para isso seria preciso um monitor **externo** (outro aparelho ou serviço), fora do escopo desta versão |

## Como saber se o Android encerrou o CDBS

```sh
cd ~/cdbs-os
python3 supervisor.py status      # "Supervisor: PARADO (último sinal 26/10 03:12)" = parou sem desligar normalmente
bash diagnostico.sh --offline     # mostra o último sinal de vida e se a última execução terminou normalmente
```

No painel, depois de iniciar de novo, o **Histórico de eventos** mostra **"Encerramento inesperado"** com a hora do último sinal de vida. Com o WhatsApp configurado, esse aviso também chega no seu número.

## Iniciar sozinho depois de reiniciar o celular (opcional)

Com o app **Termux:Boot** instalado e aberto uma vez:

```sh
mkdir -p ~/.termux/boot
cat > ~/.termux/boot/cdbs.sh <<'EOF'
#!/data/data/com.termux/files/usr/bin/sh
termux-wake-lock
cd ~/cdbs-os && bash start.sh >> data/system/boot.log 2>&1
EOF
chmod +x ~/.termux/boot/cdbs.sh
```

Nesse modo o CDBS roda sem uma tela do Termux aberta, e o que ele escreveria no terminal vai para `data/system/boot.log`. Para parar, use `python3 supervisor.py stop` (3.0).

## Testes que só dá para fazer no aparelho (PENDENTES)

Não foram executados por mim: não tenho acesso ao seu celular. Faça cada um e anote o resultado:

1. Tela desligada por 1 hora com o CDBS rodando → o site abre de outro celular? O histórico mostra alguma queda?
2. Economia de bateria ligada → mesmo teste.
3. Desligar o Wi-Fi por 5 minutos e religar → o túnel volta? O relatório marca "inconclusivo" durante a queda?
4. Fechar o Termux à força (Configurações → Apps → Termux → Forçar parada) e abrir de novo → `bash start.sh` mostra "encerramento inesperado" com o último sinal de vida?
5. Reiniciar o celular (com e sem Termux:Boot) → o que acontece?
