# Testes de navegador (ponta a ponta)

São os testes que abrem o painel num **Chromium de verdade**, numa tela de celular (390 × 844, toque). Eles conversam com um CDBS de verdade, iniciado numa **cópia** desta pasta. Servem para conferir o que os testes de unidade não veem:
- botões e diálogos;
- mensagens na tela;
- rolagem horizontal no celular;
- o caminho completo painel → servidor → guardião → túnel → WhatsApp.

Rodam **num computador com Linux**, não no celular: precisam de Node 18+ e do Playwright com o Chromium.

```sh
npm install playwright && npx playwright install chromium
bash tests/e2e/rodar.sh ops        # painel 3.0 (≈2 min)
bash tests/e2e/rodar.sh backups    # Central de backups (2.1)
bash tests/e2e/rodar.sh adega adega-corujao-site-portatil-2.zip [foto-grande.jpg]
```

| Arquivo | O que testa |
|---|---|
| `ops.js` | CDBS iniciado por `bash start.sh` (com o guardião). O teste passa por:<br>• login e visão geral;<br>• 10 posições: publicar, verificar, pausar → 503 → retomar;<br>• remover → "Sites removidos" → recuperar;<br>• histórico com filtros;<br>• notificações: número mascarado, credenciais só-gravação, mensagem de teste como modelo de 12 variáveis;<br>• webhook: aperto de mão, assinatura errada → 401, status "entregue";<br>• bot: desconhecido sem resposta, `!Status`, reenvio ignorado, `!Link` em lista, texto com `rm -rf` recusado, `!Link 6` + SIM → guardião liga o túnel → resultado real;<br>• desligar túnel e reiniciar servidor (a sessão continua);<br>• atualização: pacote inválido recusado, instalar versão +1, voltar;<br>• credenciais fora de todos os arquivos de registro;<br>• sem rolagem horizontal;<br>• sair. |
| `backups.js` | proteção por senha, backup criptografado, baixar/importar, restaurar, legado, excluir, retenção, recuperar loja removida, backup corrompido |
| `adega.js` | site da adega com o botão escondido, painel do cliente dentro do site, foto grande reduzida, pedido pelo WhatsApp |
| `fake_smtp.py` | servidor de e-mail **de mentira** (127.0.0.1:28092): confere usuário e senha e registra as mensagens, sem guardar a senha |
| `fake_meta.py` | API da Meta **de mentira** (127.0.0.1:28090): confere o token e registra o que o CDBS mandou, sem guardar o token |
| `rodar.sh` | monta a cópia, as portas de teste (28088–28092), o servidor de e-mail de mentira, o cloudflared de mentira (`tests/fake_cloudflared.py`), roda e desliga tudo |

**O que os valores de teste são.** Os tokens, chaves e números que aparecem nestes arquivos são **falsos** e só existem para o teste. A pasta original (com os seus dados) nunca é tocada.
