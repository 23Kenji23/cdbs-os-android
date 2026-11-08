# CDBS.OS

**Código-fonte do CDBS.OS — versão 4.0.0.** Plataforma de gerenciamento de sites, servidores e infraestrutura digital. Projeto em desenvolvimento contínuo.

© 2026 CDBS.OS — All Rights Reserved. Veja [LICENSE](LICENSE).

> O kit de Loop Engineering usado para criar o projeto está em [`kit-inicial/`](kit-inicial/).

---

# CDBS.OS — conceito

> **Isto é um estudo de caso, não um produto.** Está aqui para mostrar *como* a ideia foi construída: a arquitetura, as decisões e os testes. Não há suporte, não há promessa de manutenção e não é para colocar em produção.

## A ideia

Transformar um celular Android (via Termux) num mini-servidor de hospedagem, publicado na internet por um túnel Cloudflare, **sem abrir portas no roteador** e **sem expor o painel de administração**.

Em uma frase: *um painel local que hospeda sites estáticos e pequenos servidores (Python/Node), vigia tudo sozinho e avisa pelo WhatsApp ou e-mail quando algo cai.*

## Conceitos que o projeto demonstra

| Conceito | Onde ver |
|---|---|
| **Guardião (supervisor)**: processo que reinicia o servidor se cair, com espera crescente e limite, e cuida do túnel | `supervisor.py`, `docs/TERMUX.md` |
| **Separação de portas**: só a porta dos sites vai para o túnel; o painel fica em `127.0.0.1` | `cdbs.py`, `docs/ARQUITETURA-3.0.md` |
| **Monitoramento por posições fixas** com estados claros (ativo, só no aparelho, fora do ar, inconclusivo, pausado, vago) | `site_monitor.py`, `docs/MONITORAMENTO.md` |
| **Servidores de usuário** em Python/Node, com verificação de saúde e implantação com volta automática | `apps.py`, `docs/SERVIDORES.md`, `docs/ARQUITETURA-4.0.md` |
| **Notificações**: tudo é registrado primeiro no histórico local; WhatsApp (API oficial) e e-mail são só canais | `notify.py`, `whatsapp.py`, `emailer.py` |
| **Bot de comandos** com números autorizados e confirmação antes de agir | `bot.py`, `docs/BOT.md` |
| **Atualização segura**: SHA-256 por arquivo, cópia de segurança, checagem de saúde e volta atrás | `updater.py`, `versioninfo.py`, `docs/ATUALIZACOES.md` |
| **Backups criptografados** | `backup.py`, `backup_crypto.py`, `docs/CRIPTOGRAFIA.md` |
| **Catálogo para clientes**: painel escondido dentro do site, API pública só de leitura | `catalog.py`, `client/`, `docs/exemplo-catalogo.html` |
| **Mascaramento de segredos** em logs e relatórios | `sysutil.py` |

## Mapa do repositório

```
*.py           núcleo (painel, guardião, monitor, notificações, backup, atualizador)
web/           painel administrativo (HTML/CSS/JS puro, sem framework)
client/        painel do cliente (catálogo)
tests/         testes automáticos (unitários e e2e com navegador simulado)
docs/          arquitetura, guias e a história de cada atualização
config/        .env.example (todos os valores são exemplos)
tools/         empacotamento e cobertura
```

Para entender o raciocínio, comece por `docs/ARQUITETURA-3.0.md` e `docs/ARQUITETURA-4.0.md`. O guia de uso original está em `docs/GUIA-ORIGINAL.md`.

## Estado real

- Testado em **Linux** (testes automáticos e navegador simulando celular).
- **Não validado em Android real.** A lista do que falta testar está no fim de `docs/TERMUX.md`.
- Cloudflare Quick Tunnel é temporário e sem garantia de disponibilidade; o Android pode suspender processos em segundo plano. Nada aqui garante funcionamento 24 horas.

## Se quiser explorar

Rodar só para estudar, no seu computador, nunca exposto à internet:

```sh
cp config/.env.example .env
python3 -m unittest discover -s tests -p "test_*.py"
```

Os valores de `.env.example` são todos fictícios. **Nunca** publique um `.env` preenchido, a pasta `data/` ou tokens de verdade.

## Aviso

Código fornecido como está, para fins de estudo e inspiração. Quem usar por conta própria é responsável pela segurança, pelos dados e pelas contas (Cloudflare, Meta, e-mail) envolvidas.
