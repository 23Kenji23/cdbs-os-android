# Arquitetura do CDBS.OS 4.0: o que mudou em relação à 3.0 e por quê

A base continua a da [3.0](ARQUITETURA-3.0.md): guardião (supervisor), servidor `cdbs.py`, túnel, pedidos por arquivo, histórico. Este documento descreve só o que a 4.0 acrescenta.

## 1. Processos

```
bash start.sh
 └─ supervisor.py run ─────────── o guardião
     ├─ filho: python3 cdbs.py serve        painel 127.0.0.1:8089 (nunca no túnel) · sites 127.0.0.1:8088 (túnel)
     │                                       + linha "monitor" agora com 20 posições
     ├─ filho opcional: cloudflared → 127.0.0.1:8088
     └─ 4.0: até 5 filhos "servidor" ─────── python3 <entrada> | node <entrada>
            • cada um num grupo de processos próprio (start_new_session) → parar encerra o programa E o que ele abriu
            • porta fixa por posição: 8101…8105 (CDBS_APP_PORT_BASE); escuta só em 127.0.0.1
            • pasta de trabalho data/apps/<nome>/atual; saída → logs/saida.log (com rotação)
```

**Por que o guardião, e não o `cdbs.py`, liga os servidores.** O `cdbs.py` é reiniciado em atualizações e travamentos. Se ele fosse o pai dos programas, cada reinício derrubaria os 5 servidores. O guardião já sabia vigiar processos com espera crescente e limite de falhas (servidor e túnel); os programas usam a mesma regra (2 s → 5 min; 6 quedas em 15 min = "parado por falhas"; 3 verificações de saúde falhas = travado).

**Como a internet chega a um servidor.** Pelo servidor dos sites (8088), que já é o único ligado ao túnel. Quando o caminho começa com o nome de um servidor (`/recados/…`), o `cdbs.py` repassa o pedido para `127.0.0.1:<porta>` e devolve a resposta **em pedaços** (sem guardar tudo na memória). Assim não existe porta nova exposta, nem segundo túnel, nem configuração no Cloudflare.

## 2. Módulos novos ou alterados

| Arquivo | O que mudou | Testes |
|---|---|---|
| `apps.py` (novo) | `AppStore`: configuração, variáveis (0600), ZIP conferido e extraído em `novo/`, troca `atual`↔`anterior`, remoção para `data/removidos/apps/`. `AppRunner`: processos, saúde, espera crescente, implantação com volta automática, limpeza de órfãos, estado em `data/system/apps.json` | `test_apps` |
| `supervisor.py` | cria o `AppRunner`, chama `apps.tick()` no laço, encerra os órfãos ao iniciar e todos ao parar, operações `app_*`; estado com `features: ["apps"]` | `test_apps`, `test_supervisor` (autoteste) |
| `control.py` | operações `app_start/stop/restart/deploy/rollback/remove` na lista fixa | `test_servidores40` |
| `cdbs.py` | repasse (proxy) em `PublicHandler`; rotas `/api/apps…` e `/api/storage`; nome único entre sites e servidores; limites de site maiores | `test_servidores40` |
| `ops.py` | `apps_view()` (estado do guardião + configuração), cartão Servidores na visão geral; recusa ações de servidor se o guardião em execução não tem `features: ["apps"]` | `test_servidores40`, `test_painel30` |
| `site_monitor.py` | 20 posições (`MAP_VERSION = 2`), migração do mapa de 10 sem renumerar, relatório com duas posições por variável | `test_monitor` |
| `notify.py`, `audit.py` | categoria **servidores**; alertas de queda, parada por falhas, implantação revertida/falha e "voltou ao ar" | `test_notify` (`test_server_app_events…`) |
| `doctor.py` | confere os servidores (Node instalado se algum usa Node, portas, pastas) | `test_doctor` (geral) + rodado à mão |
| `web/nav.js` (novo) | páginas por `#endereço`; links antigos (`#bkSection`) levam à página certa; selos no menu | e2e |
| `web/apps.js` (novo) | página Servidores e cartão Armazenamento | e2e, teste manual |

## 3. Decisões

| Decisão | Motivo | Alternativa descartada |
|---|---|---|
| Servidores em `/<nome>/`, mesmo túnel | nada novo para configurar no Cloudflare; funciona com o Quick Tunnel | um túnel ou subdomínio por servidor (precisa de domínio próprio e mais processos) |
| Sites e servidores dividem os nomes | `/adega/` nunca fica ambíguo | prefixo `/app/<nome>/` (endereços mais feios) |
| Só Python e Node, com entrada fixa | o CDBS executa `python3 <arquivo>` ou `node <arquivo>` e mais nada: **nenhum texto digitado vira comando** | campo "comando de início" livre (seria um terminal aberto pela web) |
| Variáveis `CDBS_*` não passam ao programa | tokens do WhatsApp e senha de e-mail ficam fora do alcance do programa pelo ambiente | herdar o ambiente inteiro |
| Implantação: `novo/` → `atual/`, antigo vira `anterior/` | volta automática se a saúde falhar; Voltar versão à mão depois | trocar arquivos no lugar (sem volta) |
| `dados/` fora do código | dados sobrevivem a cada implantação | o programa gravar na própria pasta (perdido na troca) |
| Sem WebSocket na 4.0 | o repasse é HTTP/1.0 em pedaços; WebSocket exige outro tipo de conexão | — fica para uma próxima versão; responde 501 claro |
| Sem isolamento (sandbox) | o Termux sem root não oferece usuário separado nem contêiner | documentado: só código de confiança |
| Painel em páginas, mesmos módulos | cada seção da 3.0 continua com o mesmo código; `nav.js` só mostra/esconde a página que a contém | reescrever o painel (risco alto, nada a ganhar) |
| Cores e larguras pelo CSSOM | a política de segurança do painel (`style-src 'self'`) bloqueia `style="…"` | afrouxar a política |
| Guardião 3.0 em execução → ações de servidor bloqueadas com aviso | o guardião antigo não conhece `app_*`; o pedido ficaria sem resposta | deixar o pedido expirar sem explicação |

## 4. Dados novos

| Pasta / arquivo | Conteúdo | Quem escreve |
|---|---|---|
| `data/apps/<nome>/app.json` | tipo, entrada, saúde, prefixo, limites, posição, porta | servidor (painel) |
| `data/apps/<nome>/env.json` (0600) | variáveis do usuário | servidor (painel) |
| `data/apps/<nome>/{atual,anterior,novo}/` | código | painel (novo), guardião (troca) |
| `data/apps/<nome>/dados/` | dados do programa | o programa |
| `data/apps/<nome>/logs/` | saída do programa | guardião |
| `data/system/apps.json` | estado, PID, reinícios, memória, CPU | guardião |
| `data/removidos/apps/` | servidores removidos | guardião |
| `data/monitoring/sites.json` | mapa agora com 20 posições (`version: 2`) | servidor |

## 5. Segurança: o que continua e o que é novo

- **Continua:** 8089 só no celular; senha do painel; confirmações; histórico; segredos 0600 e nunca devolvidos pela API.
- **Repasse:** cabeçalhos de conexão ("hop-by-hop") retirados; `X-Forwarded-*` adicionados; corpo limitado (25 MB padrão), sem `chunked`; tempo limite (60 s padrão); `Location` e `Path=` dos cookies corrigidos para o prefixo do servidor.
- **ZIP de servidor:** sem links simbólicos, sem `..`, sem caminho absoluto, limites de tamanho e de quantidade, extraído numa pasta nova antes de tocar no que está no ar.
- **Ações:** remover exige digitar o nome; desligar, reiniciar e voltar versão pedem confirmação; tudo registrado na categoria **servidores**.
- **Limite honesto:** o programa roda com as permissões do CDBS. Ver [`SERVIDORES.md`](SERVIDORES.md#-segurança-leia-antes-de-usar).
