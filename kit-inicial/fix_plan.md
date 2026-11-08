# fix_plan.md — tarefas priorizadas do CDBS.OS

Legenda: `[ ]` a fazer · `[x]` feito (testes passando) · `[!]` travou, ver `NOTAS.md`.
Uma tarefa por volta, sempre a primeira `[ ]`. Cada item inclui o teste.

## Parte 1 — Base confiável (specs 00, 01, 02)
- [ ] `config.py`: ler `.env` e variáveis `CDBS_*` com padrões (`CDBS_SITE_PORT`=8088, `CDBS_ADMIN_PORT`=8089); teste de padrão e de sobrescrita
- [ ] `sysutil.py`: funções de mascaramento (token, telefone, e-mail); teste com valores falsos
- [ ] `settings.py`: guardar configurações e segredos em `data/` com permissão só do dono; teste de leitura, escrita e permissão
- [ ] `versioninfo.py` e `VERSION.json`: gerar lista de arquivos com SHA-256 e tamanho; teste de hash
- [ ] `cdbs.py`: servidor de sites estáticos na porta dos sites, servindo `data/sites/<nome>/`; teste de resposta 200 e 404
- [ ] 10 posições fixas de sites e validação do nome (só `a-z`, `0-9`, `-`; rejeita `..` e barras); teste de nomes inválidos
- [ ] Painel em servidor separado, só em `127.0.0.1`; teste que confirma o endereço de escuta
- [ ] Login do painel: senha com hash e sal, sessão com validade, limite de tentativas; testes de senha errada e bloqueio
- [ ] Publicar site por `.zip`: tamanho máximo, bloqueio de zip-slip, exigir `index.html`; testes com zip malicioso
- [ ] Remover site movendo para `data/removidos/`; teste de que nada é apagado direto
- [ ] Painel web: tela de visão geral e lista das 10 posições (HTML/CSS/JS puros, layout de celular); `node --check` e teste das rotas

## Parte 2 — Operação confiável (specs 03, 04, 05, 06)
- [ ] `supervisor.py`: iniciar e vigiar o servidor de sites; reiniciar com espera crescente e limite; teste com processo que cai
- [ ] Supervisor: instância única e limpeza de processos órfãos; teste de segunda instância e de órfão
- [ ] `start.sh` com modo `--verificar`; teste do script
- [ ] `tests/fake_cloudflared.py` e túnel `quick`: iniciar, ler o endereço da saída, detectar queda e troca de endereço; testes com o falso
- [ ] Modos do túnel (`quick`, `named`, `off`), autostart e `CDBS_PUBLIC_BASE`; testes de cada modo
- [ ] `site_monitor.py`: checagem local das 10 posições com os estados `ativo`, `fora do ar`, `pausado`, `vago`; testes por estado
- [ ] Monitor: checagem pública opcional e estados `só no aparelho` e `inconclusivo`; testes de rede indisponível
- [ ] Monitor: notificar só quando o estado muda e persistir o estado em disco; teste de mudança e de reinício
- [ ] `notify.py`: histórico local primeiro, canais depois; teste de que canal que falha não perde o evento
- [ ] `whatsapp.py`: envio pela Cloud API com modelos de mensagem, usando servidor falso; teste de sucesso, erro e token mascarado
- [ ] `emailer.py`: envio SMTP com servidor falso; teste de sucesso, erro e e-mail mascarado
- [ ] `doctor.py`: diagnóstico do ambiente e do estado do sistema, com segredos mascarados; teste de relatório
- [ ] Painel: telas de túnel, monitoramento e notificações; `node --check` e teste das rotas

## Parte 3 — Dados, atualização e controle (specs 07, 08, 09, 10)
- [ ] `backup_crypto.py`: criptografar e descriptografar com senha; testes de senha errada e arquivo corrompido
- [ ] `backup.py`: criar, listar, verificar integridade e restaurar com cópia prévia; testes de ida e volta
- [ ] Backup bloqueado sem a biblioteca de criptografia; teste simulando a ausência
- [ ] `updater.py`: validar pacote (SHA-256, caminhos, não tocar em `data/` e `.env`); testes com pacote adulterado
- [ ] Atualizador: instalar com cópia de segurança, checagem de saúde e volta automática; teste de falha forçada
- [ ] `atualizar.sh` e botão de atualizar no painel; testes do script e da rota
- [ ] `apps.py` e `control.py`: implantar servidor Python por `.zip` numa porta própria; teste de implantação
- [ ] Servidores de usuário: contrato `/saude`, iniciar, parar, reiniciar, voltar versão, remover; testes de cada operação
- [ ] Servidores Node via `CDBS_NODE_BIN` e isolamento de falha; testes
- [ ] `bot.py`: webhook com verificação de assinatura e lista de números autorizados; testes de assinatura inválida e número não autorizado
- [ ] Bot: comandos de lista fixa, confirmação antes de agir, idempotência e limite de uso; testes
- [ ] Painel: telas de backups, servidores e bot; `node --check` e teste das rotas

## Fechamento
- [ ] `README.md` honesto (estado real, o que não foi testado) e `docs/ARQUITETURA.md`
- [ ] Varredura final de segredos e checklist de `specs/99-checklist-pos-implementacao.md`
