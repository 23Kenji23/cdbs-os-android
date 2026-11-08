# CDBS.OS — Plano mestre de implementação (Partes 1, 2 e 3)

**Documento principal e consolidado para o Claude.** Este arquivo reúne a ordem de execução, todos os requisitos anteriores e as melhorias adicionais aprovadas para o CDBS.OS. Os documentos individuais originais também permanecem no ZIP como especificações detalhadas complementares.

**Estado real:** este documento especifica o trabalho a executar. A inclusão das especificações no ZIP não significa que os recursos estejam implementados ou que os testes tenham sido executados/aprovados.

## Regras gerais obrigatórias

1. Inspecione o repositório inteiro antes de alterar a arquitetura. Identifique módulos, rotas, páginas, scripts, tarefas em segundo plano, dados, configurações, integrações e testes existentes.
2. Implemente na ordem **Parte 1 → Parte 2 → Parte 3**. Não pule etapas nem declare uma fase concluída sem evidências.
3. Claude está autorizado a criar, alterar, mover e reorganizar arquivos, módulos, rotas, interface, scripts, configurações, migrações e testes quando necessário. Não fique preso à arquitetura existente, mas explique mudanças relevantes.
4. Preserve os dados do usuário, credenciais, configurações e funcionalidades existentes. Faça backup antes de qualquer migração ou alteração destrutiva.
5. Não invente resultados. Informe comandos executados, ambiente, aprovados, falhas, ignorados, limitações e cobertura medida. Nunca afirme que testes passaram se não foram executados.
6. Use operações explícitas e permitidas para cada ação administrativa; não execute texto recebido do WhatsApp como comando de shell.
7. Quando uma integração exigir credenciais ou configuração externa, implemente configuração segura e testes simulados; documente exatamente o que falta para o teste real.

---

# PARTE 1 — Base confiável, segurança, diagnóstico, backups e testes

## 1.1 Inventário técnico inicial

- Mapear o projeto completo: `cdbs.py`, catálogo, backup/criptografia, painel web, cliente, scripts de inicialização, Cloudflare Tunnel, atualizador, monitoramento, notificações, bot e testes.
- Registrar o fluxo atual de inicialização, publicação, remoção, restauração e atualização.
- Identificar dados persistentes e arquivos que não podem ser sobrescritos.
- Antes de corrigir ou substituir qualquer módulo, registrar o comportamento atual e os riscos de regressão.

## 1.2 Testes obrigatórios em todos os setores

Criar/atualizar testes unitários, de integração, segurança, regressão e ponta a ponta para todos os módulos e fluxos relevantes. A matriz mínima deve cobrir:

- `cdbs.py`, catálogo, validações e operações de publicação/remoção;
- backups, restauração, criptografia, integridade e tratamento de corrupção;
- painel web, rotas administrativas, formulários e validação de entradas;
- cliente, scripts de instalação/inicialização e compatibilidade com Termux/Android;
- Cloudflare Tunnel, estados de conectividade e falhas de processo;
- atualizador, validação de pacote, instalação, verificação de saúde e rollback;
- monitoramento dos 10 espaços, notificações e persistência do estado;
- bot WhatsApp, autorização, comandos, sessões, confirmações, repetição de eventos e limites de uso;
- concorrência, recuperação após interrupção, segurança e regressão geral.

Requisitos de qualidade:
- Cada módulo/setor deve ter pelo menos um teste significativo; cobrir caminhos críticos de sucesso e falha.
- Usar mocks/fakes para WhatsApp, Cloudflare, rede e processos externos nos testes automatizados; reservar testes reais para ambiente configurado.
- Executar a suíte existente e a nova suíte. Corrigir falhas causadas pelas alterações e investigar regressões.
- Não confundir “todos os módulos têm testes” com “100% de cobertura de linhas”. Medir e relatar a cobertura disponível.
- Entregar relatório com comandos exatos, ambiente, totais de aprovados/falhos/ignorados, cobertura, limitações e itens não testados.
- Testar em Android/Termux real quando disponível. Se não estiver disponível, marcar claramente como pendente.

## 1.3 Backup, restauração e recuperação

- Fazer backup automático antes de atualização, migração ou operação destrutiva.
- Validar integridade do backup e indicar data, tamanho e resultado.
- Ter procedimento de restauração que não sobrescreva dados silenciosamente.
- Criar testes de restauração, corrupção, arquivo ausente, falta de espaço e interrupção durante a operação.
- Quando aplicável, manter versão anterior recuperável até a nova versão passar nas verificações.
- Nunca registrar segredos no log ou incluir credenciais em arquivos de exemplo/relatórios.

## 1.4 Auditoria e histórico de eventos

Registrar, com horário e resultado: início/parada de serviços, publicação/remoção, atualizações, rollback, backup/restauração, erros do monitoramento e comandos administrativos do bot. Registrar motivo e identificador da ação quando disponível, mas mascarar tokens, senhas, códigos e dados sensíveis.

## 1.5 Diagnóstico `cdbs.py doctor`

Criar um comando de diagnóstico equivalente a `python cdbs.py doctor` ou adaptar ao padrão real do projeto. Deve verificar, sem modificar dados por padrão:

- dependências e versões relevantes;
- permissões de arquivos e diretórios;
- configurações obrigatórias ausentes ou inválidas;
- espaço livre e acesso aos diretórios de dados/backups;
- estado dos processos e do Cloudflare Tunnel;
- integridade/configuração básica dos 10 espaços;
- conectividade disponível e situação da integração WhatsApp, sem exibir segredos.

O comando deve retornar resumo legível, status por verificação e sugestões acionáveis. Só executar correções automáticas mediante opção explícita e com backup quando necessário.

## 1.6 Android/Termux e continuidade operacional

- Documentar dependências do Android, permissões e limitações de execução em segundo plano.
- Testar, quando possível, tela apagada, economia de bateria, perda/retorno de internet, encerramento do Termux/processo e reinicialização do celular.
- Detectar e explicar quando o processo foi encerrado ou não pode iniciar; não prometer disponibilidade 24/7 se o celular, internet ou processo estiverem desligados.
- Fornecer instruções para remover restrições de bateria quando aplicável, sem afirmar que isso garante execução permanente.

## 1.7 Critérios para concluir a Parte 1

- Inventário técnico documentado.
- Testes existentes executados e novas coberturas implementadas.
- Backup/restauração e proteção de dados cobertos por testes.
- Diagnóstico documentado e testado.
- Relatório honesto de resultados e pendências.

---

# PARTE 2 — Watchdog, monitoramento dos 10 espaços, notificações e atualizador seguro

## 2.1 Monitoramento real dos 10 espaços

- Manter exatamente 10 posições identificáveis, cada uma com site atribuído ou estado “vaga”.
- Padronizar estados como ativo/saudável, indisponível, iniciando, parado, configuração ausente, erro e vaga, conforme o estado real e o modelo existente.
- Distinguir processo iniciado de site realmente acessível. Quando aplicável, testar o serviço local e o link público, sem inferir que está funcionando só porque existe um processo.
- Registrar última verificação, duração, resultado e motivo da falha.
- Uma falha em um site não deve derrubar o painel nem as demais publicações.

## 2.2 Watchdog e agendamento

- Executar verificações periódicas, com intervalo padrão de 30 minutos para o relatório programado, respeitando a configuração definida.
- Evitar verificações concorrentes sobre a mesma posição.
- Detectar travamentos, falhas de processo e indisponibilidade; registrar incidentes e enviar alertas com limitação de frequência.
- Não reiniciar serviços em loop. Qualquer tentativa de recuperação automática deve ter limite, espera progressiva, registro e proteção contra reinícios repetidos.
- A rotina de monitoramento deve tratar falhas de rede/WhatsApp sem bloquear o painel nem o serviço principal.

## 2.3 Central de notificações e WhatsApp

- Criar uma central local de notificações com histórico, horário, categoria, posição afetada, estado e resultado de entrega.
- Enviar ao administrador um resumo periódico das 10 posições e alertas importantes por WhatsApp, com opção de configurar destinatários e intervalo.
- Evitar spam: agrupar eventos repetidos, aplicar cooldown e enviar recuperação quando um serviço voltar a funcionar.
- Usar integração oficial/compatível, preferencialmente WhatsApp Business Platform Cloud API. Configurar tokens, identificadores e destinatários por variáveis de ambiente ou configuração protegida.
- Não presumir que qualquer conta pessoal do WhatsApp possa receber automação direta. Documentar a configuração externa necessária e usar mocks nos testes automatizados.
- Se a API estiver indisponível, manter o evento no histórico e registrar a falha de entrega.

## 2.4 Painel operacional

Exibir visão geral e lista das 10 posições com estado real, site/domínio, link público, última verificação, último erro e ações permitidas. Mostrar também estado do Tunnel, última notificação, último backup e atualização instalada. Os controles devem pedir confirmação para ações disruptivas e mostrar o resultado retornado pelo sistema.

## 2.5 Atualizador pelo painel com rollback

- Permitir ao administrador enviar/selecionar um pacote de atualização pelo painel, conforme a arquitetura mais segura viável.
- Validar extensão, tamanho, estrutura, integridade e origem/assinatura quando disponível. Nunca executar arquivos arbitrários do pacote sem validação.
- Antes de instalar: criar backup validado, verificar espaço livre, bloquear atualizações concorrentes e preservar dados persistentes, credenciais e configuração local.
- Instalar em etapa controlada; executar verificações de saúde após a atualização.
- Se validação, instalação ou verificação falhar, restaurar a versão anterior quando possível e informar o resultado claramente.
- O mecanismo de recuperação não pode depender exclusivamente do processo principal que pode ser interrompido pela atualização. Criar estratégia auxiliar compatível com Termux, se tecnicamente viável, e documentar suas limitações.
- Manter log de versão anterior/nova, data, etapa, resultado e rollback, sem segredos.

## 2.6 Proteção contra concorrência e isolamento

- Impedir que duas ações simultâneas atualizem/reiniciem o mesmo serviço.
- Usar lock/mecanismo equivalente com liberação segura após falhas e timeout para lock abandonado.
- Separar ciclo de vida do painel dos processos de cada site quando possível; erro em um site não deve derrubar os demais.
- Testar falha parcial, indisponibilidade do WhatsApp, perda de rede e interrupção do atualizador.

## 2.7 Critérios para concluir a Parte 2

- As 10 posições têm estados rastreáveis e monitoramento testado.
- O watchdog não cria loops de reinício nem tarefas duplicadas.
- Alertas periódicos e alertas por mudança de estado são testáveis, com controle de repetição.
- O atualizador protege dados, cria backup, verifica saúde e tenta rollback.
- Falhas de um site ou de uma integração externa não derrubam o restante do sistema.

---

# PARTE 3 — Bot WhatsApp `!Link`, controle administrativo e painel profissional

## 3.1 Comandos básicos

Implementar os comandos abaixo, com respostas curtas e claras:

- `!Link` — abre o menu principal de gerenciamento das publicações.
- `!Status` — mostra resumo do estado real do CDBS.OS, Tunnel e 10 posições.
- `!Relatorio` — gera relatório detalhado.
- `!Ajuda` — lista comandos e explica como usá-los.
- `!Cancelar` — cancela a sessão/ação pendente do usuário.

## 3.2 Menu `!Link`

Ao receber `!Link`, apresentar as opções:

1. Listar as 10 posições e seus estados.
2. Mostrar links públicos ativos.
3. Verificar um site específico.
4. Iniciar ou recuperar a publicação de um site.
5. Verificar o Cloudflare Tunnel.
6. Iniciar/reiniciar o Tunnel.
7. Mostrar posições vagas.
8. Gerar relatório completo.

Depois da escolha, pedir a posição/site quando necessário. Usar botões/listas interativas se o provedor suportar; caso contrário, apresentar menu numerado em texto. Permitir cancelar e expirar sessões abandonadas. O bot deve consultar o estado real antes de responder, sem inventar que uma publicação está ativa.

## 3.3 Confirmação e limites de ação

- Consultas de estado podem responder diretamente após autorização.
- Iniciar, parar ou reiniciar processos, alterar publicação ou atualizar o sistema exige confirmação explícita antes da execução.
- Informar qual ação será executada e qual site/serviço será afetado.
- Retornar sucesso apenas depois de receber o resultado real da operação; em falha, mostrar erro útil sem expor detalhes secretos.
- Mapear cada opção para uma função interna allowlist. Nunca passar o texto recebido como comando de shell.

## 3.4 Segurança obrigatória do bot

- Lista de números autorizados em formato internacional normalizado.
- Validar assinatura do webhook conforme o provedor e verificar origem/autenticidade dos eventos.
- Prevenir repetição de mensagens/eventos (idempotência), limitar frequência e expirar sessões.
- Não aceitar comandos administrativos de números não autorizados.
- Proteger tokens em variáveis de ambiente/configuração protegida; nunca gravá-los no código, logs ou respostas.
- Separar permissão de consulta da permissão de alterar o sistema.
- Registrar auditoria de comandos autorizados, recusados e ações realizadas, mascarando dados sensíveis.
- Não disponibilizar controle administrativo para clientes ou público.

## 3.5 Integração real e limitações

O bot exige um provedor/API/webhook compatível, credenciais válidas e configuração do lado do provedor. Implementar testes simulados sem credenciais. Teste real só pode ser declarado concluído depois de enviar e receber mensagens em uma configuração real. Se o CDBS.OS e o bot estiverem no mesmo Android, sua disponibilidade depende do celular, processo e internet estarem operacionais.

## 3.6 Painel profissional e diagnóstico acionável

- Visão geral com estados, links, erros recentes, última verificação, backup e versão instalada.
- Lista de 10 posições, incluindo vagas, com ações individuais.
- Histórico de incidentes e eventos com filtros básicos.
- Mensagens de erro que expliquem o problema e próximos passos, sem expor segredos.
- Ações críticas com confirmação, bloqueio de cliques repetidos e retorno de resultado.
- Layout responsivo para celular e desktop, preservando os recursos existentes.

## 3.7 As sete melhorias adicionais — incorporadas ao plano

1. **Recuperação e segurança:** backup antes de atualizações, restauração testada, preservação de dados e possibilidade de voltar à versão anterior.
2. **Monitoramento real:** checagem de painel, sites e Tunnel, detecção de travamento, motivo da falha, última verificação e alertas sem repetição excessiva.
3. **Operação Android:** diagnóstico de encerramento de processo, bateria, permissões, internet e reinicialização; documentação realista das limitações de segundo plano.
4. **Bot WhatsApp:** menu `!Link`, comandos básicos, confirmação de ações, histórico e estados consultados de verdade.
5. **Auditoria:** histórico de publicações, reinícios, atualizações, restaurações, erros e comandos administrativos, sem segredos.
6. **Testes e diagnóstico:** teste de todos os setores, comando `doctor`, simulações de falhas e relatório honesto.
7. **Painel profissional:** estado geral, 10 posições, controles individuais, histórico, espaço e backup, com mensagens acionáveis.

## 3.8 As três proteções adicionais — obrigatórias

1. **Evitar conflitos:** impedir duas ações simultâneas sobre o mesmo serviço, especialmente reinicializações e atualizações. Usar locks, timeout e tratamento de falhas.
2. **Atualização segura:** validar o pacote, criar backup, instalar de forma controlada, verificar saúde e tentar rollback se houver falha. Não ativar uma atualização sem verificar o resultado.
3. **Separar controle e publicação:** uma falha em um site não deve derrubar o painel administrativo nem outras publicações. Isolar processos quando viável e testar falhas parciais.

## 3.9 Critérios para concluir a Parte 3

- Todos os comandos documentados respondem conforme a autorização.
- Menu numerado funciona mesmo sem botões interativos.
- Ações destrutivas pedem confirmação e podem ser canceladas.
- Testes cobrem autorização, recusas, repetição, sessão expirada, falha do provedor e falha de processo.
- O painel apresenta estados reais e não declara sucesso sem confirmação.

---

# Ordem de entrega e relatório final obrigatório

1. Concluir Parte 1 e apresentar inventário, testes e riscos encontrados.
2. Concluir Parte 2, executar testes de monitoramento, notificações e atualizador.
3. Concluir Parte 3, executar testes do bot e painel.
4. Executar novamente toda a suíte de testes e testes de regressão ao final.
5. Entregar resumo com arquivos criados/alterados, decisões arquiteturais, comandos executados, resultados reais, cobertura medida, testes pendentes e configuração externa necessária.
6. Não apagar os documentos de especificação originais; mantê-los no ZIP como referência detalhada.

---

# Anexos — especificações detalhadas anteriores incorporadas

As seções abaixo reproduzem integralmente os documentos detalhados já existentes no projeto. Em caso de aparente conflito, siga a regra mais segura e documente a decisão; este plano mestre define a ordem geral das fases.


---

## Anexo 1 — ATUALIZACAO-CDBSOS-TESTES-BOT-WHATSAPP.md

# CDBS.OS — Testes obrigatórios em todos os setores + bot de comandos WhatsApp

**Tipo:** especificação obrigatória para implementação pelo Claude.  
**Estado:** requisitos planejados; a inclusão deste documento no ZIP não significa que o bot ou os testes já estejam implementados/executados.

## 1. Autoridade de implementação

Claude tem autorização para inspecionar e modificar o código existente para implementar esta especificação: criar módulos, rotas, telas, scripts, configurações, migrações e testes; refatorar componentes quando necessário; corrigir defeitos encontrados; adaptar a arquitetura proposta ao código real. Os `.md` descrevem o resultado desejado e critérios de aceitação, não são uma ordem para preservar código inadequado.

Não remover funções existentes sem justificativa e sem testes de regressão. Preservar sites, catálogos, credenciais, configurações, backups e demais dados persistentes. Não declarar implementação, cobertura ou testes como concluídos sem executar e registrar evidências reais. Se algo não puder ser testado no ambiente disponível, marcar explicitamente como **não testado** e explicar como testar no Termux/Android.

## 2. Testes obrigatórios em todos os setores

Antes de entregar uma versão, fazer inventário de todos os módulos, endpoints, telas, scripts, tarefas em segundo plano e integrações. Cada setor deve ter testes adequados; não basta executar somente a suíte já existente.

### 2.1 Matriz mínima

- **Servidor/backend (`cdbs.py`):** rotas, métodos HTTP, códigos de resposta, validação de entrada, erros, concorrência quando relevante, autenticação/autorização e limites de tamanho.
- **Catálogo (`catalog.py` e interfaces relacionadas):** criação, leitura, edição, exclusão, dados inválidos, persistência e regressão.
- **Backups (`backup.py`, `backup_crypto.py`):** criação, listagem, restauração, corrupção, arquivo ausente, integridade, criptografia, senhas/chaves incorretas e preservação de dados.
- **Painel web (`web/`):** estados de carregamento, sucesso, falha e vazio; chamadas da API; upload; menus; mensagens de erro; ausência de credenciais ou dados sensíveis no navegador.
- **Cliente (`client/`):** fluxos existentes e comunicação com backend.
- **Scripts (`start.sh`, `start-cloudflare.sh`, `atualizar.sh`, diagnóstico e novos scripts):** sintaxe, pré-requisitos ausentes, permissões, caminhos com espaços quando aplicável, falhas de comando e códigos de saída. Não executar ações destrutivas no teste real.
- **Atualizador:** ZIP inválido, caminho de arquivo malicioso (Zip Slip), arquivos obrigatórios ausentes, versão incompatível, checksum/integridade, pouco espaço, falha de instalação, falha ao reiniciar, health check, rollback e preservação de dados.
- **Monitoramento/notificações/WhatsApp:** estados Ativo/Fora do ar/Vago/Inconclusivo, timeouts, internet indisponível, duplicidade, tentativas, limites do provedor, credenciais ausentes e destinatário não autorizado.
- **Integração ponta a ponta:** painel → API → lógica → persistência → resposta; bot → autenticação → ação → confirmação; atualizar → reiniciar → verificar → rollback.
- **Segurança:** autenticação, autorização por número permitido, CSRF/origem quando aplicável, injeção de comandos, traversal, upload abusivo, exposição de segredos, replay/duplicidade de webhooks e rate limiting.
- **Regressão:** executar também a suíte já existente e verificar que funcionalidades anteriores continuam funcionando.

### 2.2 Qualidade e relatório

1. Criar/atualizar testes unitários para cada módulo lógico e testes de integração para cada fluxo crítico.
2. Usar mocks/fakes para WhatsApp, Cloudflare e operações de processo; não enviar mensagens reais nem reiniciar o servidor de produção durante testes automatizados.
3. Rodar os testes existentes e os novos. Corrigir falhas antes de marcar a entrega como pronta.
4. Medir cobertura quando houver ferramenta compatível. Meta inicial: 100% dos setores/módulos inventariados cobertos por pelo menos um teste relevante; para código crítico, cobrir caminhos de sucesso e falha. Não confundir 100% dos módulos testados com 100% de cobertura de linhas.
5. Registrar comandos realmente executados, quantidade de testes passados/falhos/ignorados, cobertura obtida, ambiente e limitações. Não inventar resultados.
6. Fazer smoke test no Termux/Android antes de produção, incluindo início e parada dos serviços, portas, túnel e acesso público. Se o ambiente não estiver disponível, marcar este item como pendente.

## 3. Bot WhatsApp: comando `!Link`

### Objetivo

O proprietário envia `!Link` por WhatsApp e recebe um menu de ações disponíveis para gerenciar/verificar links do CDBS.OS. O comando não deve executar uma ação ambígua imediatamente: primeiro apresenta opções e pede que o usuário escolha.

### Exemplo de conversa

```text
Usuário: !Link

CDBS.OS — MENU DE LINKS
Escolha uma opção:
1. Listar os 10 espaços e seus estados
2. Ver links públicos ativos
3. Verificar um site específico
4. Iniciar/subir a publicação de um site
5. Verificar o estado do Cloudflare Tunnel
6. Iniciar/reiniciar o Tunnel
7. Ver posições vagas
8. Receber relatório completo

Responda com o número da opção. Digite !Cancelar para sair.
```

Depois da escolha, o bot deve pedir o identificador do site/posição quando necessário, mostrar um resumo da ação e retornar resultado real. Exemplo:

```text
Usuário: 4
Bot: Qual site/posição deseja iniciar? Responda com o número ou nome.
Usuário: 2
Bot: Você pediu para iniciar a publicação da posição 2 (nome-do-site). Confirmar? SIM / CANCELAR
Usuário: SIM
Bot: Resultado: ... (baseado no estado verificado, nunca presumido)
```

A opção 4 só pode ser oferecida se o projeto realmente tiver uma operação segura e suportada para iniciar/publicar o site selecionado. O bot não deve inventar que “subiu” só porque iniciou um processo: confirmar que o processo está ativo e, quando possível, que o endereço público responde. Diferenciar falha local, falha de túnel e verificação inconclusiva.

### Comandos básicos

- `!Link` — abre o menu de links.
- `!Status` — estado geral do CDBS.OS, servidor de sites e Tunnel, quando verificável.
- `!Relatorio` — envia o relatório das 10 posições.
- `!Ajuda` — mostra comandos e exemplos.
- `!Cancelar` — cancela a seleção pendente.

Opcionalmente, aceitar `!Link 2` como atalho para listar links, mas manter `!Link` sem argumento como menu. Normalizar espaços e maiúsculas/minúsculas sem aceitar comandos parecidos de forma perigosa.

### Menu interativo e fallback

Usar listas/botões interativos do provedor oficial quando suportados pela integração escolhida. Implementar também menu em texto numerado, pois o suporte a mensagens interativas depende do provedor/API e do tipo de conversa. Validar a resposta contra as opções que foram realmente apresentadas e ainda estão válidas.

### Segurança obrigatória

- Aceitar comandos administrativos apenas de números de WhatsApp previamente autorizados, comparados em formato internacional normalizado.
- Verificar a assinatura/autenticidade do webhook do provedor e rejeitar eventos inválidos; proteger contra reenvio e processar IDs de mensagem de forma idempotente.
- Não executar texto recebido como shell/comando do sistema. Mapear opções para funções internas permitidas; nunca concatenar entrada do WhatsApp em comandos de terminal.
- Exigir confirmação explícita para iniciar/reiniciar serviços ou qualquer ação que possa interromper sites.
- Não permitir que mensagens de visitantes/clientes controlem o servidor. Respostas públicas não devem revelar caminhos locais, tokens, senhas ou logs sensíveis.
- Aplicar limites de frequência, expiração do menu/sessão e logs sem segredos.
- Guardar tokens e segredos em configuração local protegida/variáveis de ambiente; não colocá-los no código, no ZIP distribuído ou nos logs.

## 4. Arquitetura sugerida

Criar módulo isolado para integração, por exemplo `whatsapp_bot.py`, com separação clara entre:

1. Adaptador de webhook/provedor e validação da assinatura.
2. Autorização e estado curto da conversa/menu.
3. Parser de comandos (`!Link`, `!Status`, `!Relatorio`, `!Ajuda`, `!Cancelar`).
4. Serviços internos reutilizáveis que consultam os estados reais dos sites/túnel e executam apenas ações permitidas.
5. Adaptador de envio de mensagens e tratamento de falhas/limites.

Não misturar lógica de bot dentro de handlers genéricos HTTP, catálogo ou backup. Reutilizar funções existentes quando forem seguras; não duplicar regras de estado dos sites.

## 5. Requisitos reais de operação

A integração de mensagens precisa de um provedor/API WhatsApp compatível, credenciais, configuração do webhook e autorização do destinatário. Não presumir que a conta pessoal do WhatsApp pode ser automatizada diretamente. Documentar os passos de configuração e deixar a integração desativada com mensagem clara enquanto faltarem credenciais.

Como o CDBS.OS roda no Android/Termux, o bot só poderá responder enquanto os processos necessários e a conexão estiverem disponíveis. Se o telefone desligar, perder internet ou o Android encerrar o processo, o bot local não pode garantir resposta. Documentar essa limitação; não prometer monitoramento externo 24/7 sem um serviço externo.

## 6. Critérios de aceitação

- `!Link` do número autorizado abre um menu e não executa ações por conta própria.
- Número não autorizado não consegue consultar informações administrativas nem executar ações.
- Seleções inválidas, menus expirados, `!Cancelar`, webhook duplicado, provedor indisponível e credenciais ausentes são tratados sem travar o servidor.
- A ação de iniciar/reiniciar só é permitida após confirmação e retorna o resultado observado.
- Estado do site e estado do Tunnel são relatados separadamente.
- Todos os setores inventariados têm testes relevantes; suíte antiga e nova são executadas; falhas e limitações são registradas.
- A documentação final distingue claramente implementado, testado, não testado e dependente de credenciais externas.


---

## Anexo 2 — ATUALIZACAO-CDBSOS-MONITORAMENTO-WHATSAPP.md

# CDBS.OS — Especificação de atualização: monitoramento de 10 sites e relatório por WhatsApp

**Objetivo:** especificar a estrutura técnica a implementar no CDBS.OS para verificar até 10 posições de sites e enviar ao proprietário um relatório completo pelo WhatsApp a cada 30 minutos.

**Base inspecionada:** pacote `CDBS-Android-Cloudflare.zip` (código Python, painel web, catálogo, backups e scripts Termux/Cloudflare).

**Estado deste documento:** plano técnico. Não significa que a funcionalidade já foi implementada ou testada.

---

## 1. Resultado esperado

A cada 30 minutos, o CDBS.OS verifica as 10 posições configuradas e envia uma única mensagem com todas elas, inclusive posições vazias e sites indisponíveis.

Exemplo ilustrativo:

```text
CDBS.OS — RELATÓRIO DE SITES
Verificado: 26/10/2026 10:30

1. 🟢 Ativo — loja-a
2. 🟢 Ativo — portfolio
3. 🔴 Fora do ar — catalogo
4. ⚪ Vago
5. ⚪ Vago
6. 🟢 Ativo — landing
7. ⚪ Vago
8. 🟡 Verificação inconclusiva — loja-b
9. ⚪ Vago
10. ⚪ Vago

Resumo: 4 ativos · 1 fora do ar · 1 inconclusivo · 4 vagos
Próxima verificação: aproximadamente 11:00
```

O exemplo contém estados fictícios apenas para ilustrar o formato.

### Estados padronizados

- **Ativo:** há site cadastrado na posição e o teste de disponibilidade configurado passou.
- **Fora do ar:** há site cadastrado, mas as verificações configuradas falharam de forma consistente.
- **Vago:** a posição não tem site associado.
- **Verificação inconclusiva:** não foi possível afirmar se o site está ativo ou fora do ar, por exemplo por falta de internet, timeout ou erro temporário do monitor.

Não marcar uma posição como “Vago” apenas porque o site está indisponível. “Vago” significa que não há site cadastrado naquela posição.

## 2. O que existe hoje na estrutura do projeto

### Backend

- `cdbs.py`: servidor Python com `ThreadingHTTPServer`, API administrativa e serviço público de sites.
- `backup.py`: lógica de backups, histórico e lembretes de backup atrasado.
- `catalog.py`: lógica dos catálogos dos clientes.
- `data/sites/`: diretório de sites publicados.
- `config/.env.example`: exemplo atual de configuração, incluindo as portas e `CDBS_PUBLIC_BASE`.

### Interface

- `web/index.html`, `web/app.js`, `web/app.css`: painel administrativo principal.
- `web/backups.js`: interface da central de backups.
- `client/`: interface auxiliar/cliente.

### Execução e publicação

- `start.sh`: inicia `cdbs.py`.
- `start-cloudflare.sh`: inicia o Quick Tunnel para a porta dos sites, normalmente `8088`.
- Painel administrativo: normalmente `127.0.0.1:8089`.
- Sites públicos: normalmente servidos na porta `127.0.0.1:8088`.
- O painel administrativo não deve ser exposto pelo túnel.

### Funcionalidade de lembretes existente

`backup.py` já contém lembretes internos para backups atrasados, verificados na inicialização e em intervalo periódico de seis horas. Isso não é um sistema geral de monitoramento dos sites e não envia mensagens ao WhatsApp.

### Consequência arquitetural

A nova função deve ser implementada como módulo separado e integrado aos pontos adequados, sem colocar lógica de WhatsApp dentro dos handlers HTTP, da lógica de backup ou da lógica de catálogo.

---

## 3. Decisão importante: o que significa “site ativo”

A verificação precisa distinguir duas coisas:

1. **Saúde local:** o CDBS está conseguindo servir o site no próprio aparelho.
2. **Acessibilidade pública:** o site pode ser acessado por fora do aparelho através do endereço público/túnel.

Um site pode estar saudável localmente e inacessível publicamente porque o Cloudflare Tunnel caiu. O relatório deve indicar a falha correta, sem atribuir automaticamente o problema ao site.

### Política recomendada

- Se a posição estiver vazia: `Vago`; não executar requisição.
- Se houver site cadastrado: fazer requisição HTTP local ao caminho daquele site e validar resposta e conteúdo mínimo esperado.
- Se houver um endereço público estável configurado e verificável: fazer também teste público separado.
- Se for utilizado Quick Tunnel e o endereço atual não estiver conhecido/persistido, não fingir que o teste público foi feito. Reportar estado público como não configurado/inconclusivo ou monitorar apenas saúde local, deixando isso explícito na configuração.
- Usar timeout curto por site, no máximo algumas tentativas limitadas e sem bloquear o servidor principal.
- Diferenciar HTTP 404/500, timeout, falha de DNS e falha de conexão nos detalhes internos; a mensagem do WhatsApp pode resumir.

**Importante:** o diretório `data/sites/` representa sites estáticos publicados. Não se deve interpretar a existência de uma pasta como prova de que o site está acessível externamente.

---

## 4. Como mapear 10 posições

Criar uma configuração persistente de 10 posições numeradas de 1 a 10.

Cada posição deve conter:

- `slot`: número fixo de 1 a 10.
- `slug`: nome do site associado, ou `null` quando vaga.
- `enabled`: se a posição participa do monitoramento.
- `expected_path`: caminho de verificação, por padrão `/` dentro do site.
- `last_status`: último estado calculado.
- `last_checked_at`: horário da última verificação.
- `last_success_at`: último sucesso, se houver.
- `last_error_code`: código interno resumido, sem credenciais nem conteúdo privado.

### Regras de atribuição

- Na primeira configuração, preencher as posições a partir dos sites existentes em ordem alfabética, somente como valor inicial; o proprietário deve poder reorganizar os sites.
- Um site não pode ocupar duas posições simultaneamente.
- Uma posição vazia deve continuar aparecendo como `Vago` no relatório.
- Ao remover um site, a posição associada volta a `Vago` após confirmar que o site realmente foi removido.
- Não renumerar silenciosamente posições quando um site é excluído; manter os números 1–10 estáveis.
- Sites além do limite de 10 não devem desaparecer silenciosamente. Mostrar aviso administrativo e definir explicitamente se ficam fora do monitoramento.

### Arquivo de configuração sugerido

`data/monitoring/sites.json` — dados locais, gerados pelo próprio CDBS.OS. Não colocar em `data/sites/`, pois essa pasta é servida publicamente.

Exemplo estrutural:

```json
{
  "version": 1,
  "slots": [
    {"slot": 1, "slug": "loja-a", "enabled": true, "expected_path": "/"},
    {"slot": 2, "slug": null, "enabled": true, "expected_path": "/"}
  ]
}
```

O exemplo mostra somente duas posições para simplificar; a implementação deve manter exatamente dez.

---

## 5. Agendamento de 30 em 30 minutos

Criar um agendador local dedicado, por exemplo em `site_monitor.py`, iniciado junto com o processo principal do CDBS.OS.

Requisitos:

- Intervalo configurável com padrão de 30 minutos.
- Evitar executar duas verificações simultâneas.
- Uma falha em um site não interrompe a verificação dos outros nove.
- Uma falha no WhatsApp não interrompe o servidor nem a próxima rodada.
- Ao iniciar, executar uma verificação inicial e depois agendar a próxima rodada, ou seguir a opção configurada claramente documentada.
- Registrar horário de início, fim, duração e resultado da rodada.
- Usar relógio local do aparelho para exibir horário, armazenando timestamps consistentes.
- Não criar uma thread nova a cada ciclo nem deixar threads acumularem.
- Encerrar o agendador de forma controlada quando o CDBS for encerrado.
- Se o Android encerrar o Termux, o agendamento local para. Não prometer execução quando o aparelho estiver desligado.

### Política de envio

- Enviar um relatório completo a cada rodada concluída, conforme solicitado.
- O relatório inclui os dez números, mesmo quando vagos.
- Se o monitor inteiro não conseguir concluir a rodada, enviar um resumo de falha apenas quando for possível distinguir falha local de falha de envio; evitar inventar o estado dos sites.
- Guardar o resultado da rodada antes de tentar o envio.
- Persistir o último relatório e o estado do envio para diagnóstico.

---

## 6. Integração WhatsApp

### Caminho recomendado: WhatsApp Business Platform Cloud API oficial

O CDBS.OS deve usar um adaptador de envio isolado, por exemplo `whatsapp_notifier.py`, para a API oficial. Não implementar automação de WhatsApp Web por QR code, scraping ou bibliotecas não oficiais como caminho padrão: isso pode ser frágil, quebrar sem aviso e colocar a conta em risco.

A integração oficial exige configuração de negócio/WhatsApp Business Platform, número de envio, credenciais de API e, para mensagens iniciadas pela empresa fora da janela de atendimento, modelo de mensagem previamente aprovado. Consultar a documentação atual antes de configurar.

Fontes de referência:

- [Meta WhatsApp Business Platform — Cloud API (coleção oficial)](https://www.postman.com/meta/whatsapp-business-platform/documentation/wlk6lh4/whatsapp-cloud-api)
- [WhatsApp API Docs — como começar e requisitos de templates](https://whatsapp.serpro.gov.br/api-docs/por-onde-comecar/)
- [WhatsApp API Docs — enviar mensagens de notificação com template](https://whatsapp.serpro.gov.br/api-docs/guia-rapido/como-enviar-mensagens-notificacao/)

### Credenciais/configuração

Adicionar apenas nomes de variáveis no `.env.example`, sem valores reais:

```dotenv
CDBS_MONITOR_ENABLED=true
CDBS_MONITOR_INTERVAL_MINUTES=30
CDBS_MONITOR_SLOTS=10
CDBS_MONITOR_PUBLIC_CHECK=false

CDBS_WHATSAPP_ENABLED=false
CDBS_WHATSAPP_PHONE_NUMBER_ID=
CDBS_WHATSAPP_ACCESS_TOKEN=
CDBS_WHATSAPP_RECIPIENT=
CDBS_WHATSAPP_TEMPLATE_NAME=
CDBS_WHATSAPP_TEMPLATE_LANGUAGE=pt_BR
```

Os nomes finais devem ser compatíveis com as convenções do código existente. `CDBS_WHATSAPP_RECIPIENT` é o número que receberá os relatórios, no formato internacional exigido pela API. O número que envia as mensagens e o número destinatário são conceitos diferentes.

- Nunca inserir tokens ou números privados diretamente no código.
- `.env` deve continuar fora do ZIP de atualização e fora do Git.
- Não mostrar token completo na interface, nos logs, no diagnóstico nem em mensagens de erro.
- Configuração ausente deve desativar o envio com status claro; o monitor local continua funcionando.
- Definir timeout HTTP, validar a resposta e registrar o identificador/status de envio retornado pela API quando disponível.
- Tratar rate limits, erros HTTP, token expirado, template inexistente/reprovado e ausência de internet.
- Não repetir envios em loop. Usar tentativas limitadas e backoff.
- Confirmar que o destinatário autorizou receber os relatórios e cumprir os requisitos vigentes da plataforma.

### Template WhatsApp

Como o relatório é iniciado automaticamente pelo sistema a cada 30 minutos, não depender de uma conversa aberta pelo destinatário. Preparar um template de utilidade compatível com a política vigente, submetê-lo à aprovação e só ativar o envio automático depois de aprovado.

O template deve permitir inserir o relatório dinâmico de maneira compatível com as regras da API. Se um único parâmetro com várias linhas não for aceito pelo template escolhido, dividir o conteúdo em parâmetros fixos ou escolher um formato aprovado alternativo. Não presumir que o template está aprovado só porque o código envia a requisição.

O monitor e o painel devem continuar funcionando sem credenciais ou enquanto o template estiver pendente.

---

## 7. Conteúdo e tamanho do relatório

- Cabeçalho com nome `CDBS.OS`.
- Data/hora da verificação.
- Linhas 1 a 10, sempre na mesma ordem.
- Nome curto do site nas posições ocupadas.
- Estado legível e ícone.
- Resumo agregado por estado.
- Informação de última verificação e, opcionalmente, próxima rodada prevista.
- Não incluir tokens, caminhos locais completos, stack traces, dados de clientes ou detalhes de segurança.

Manter a mensagem curta o suficiente para uma leitura rápida. Se houver muitos detalhes de erro, enviar apenas o resumo no WhatsApp e manter os detalhes técnicos no painel local.

---

## 8. Interface administrativa

Adicionar uma seção **Monitoramento** ao painel administrativo existente, sem substituir o layout atual nem quebrar o fluxo de publicação.

### Cartões/indicadores

- Ativos agora.
- Fora do ar.
- Verificação inconclusiva.
- Vagos.
- Última rodada e duração.
- Estado da integração WhatsApp: desativada, aguardando configuração, pronta, último envio falhou ou último envio aceito.

### Lista de 10 posições

Cada linha/cartão mostra:

- Número da posição.
- Nome do site ou `Vago`.
- Estado atual.
- Última verificação.
- Último sucesso.
- Resultado local e, se configurado, resultado público.
- Ação para associar/desassociar um site, sem permitir duplicidade.

### Controles

- Botão **Verificar agora**.
- Botão **Enviar mensagem de teste** somente quando WhatsApp estiver configurado, com confirmação explícita.
- Configurar destinatário, intervalo e modo de verificação por UI ou arquivo de configuração documentado.
- Mostrar erros sem revelar tokens.
- Histórico das últimas rodadas e dos últimos envios.

A interface deve funcionar em telas pequenas e manter a administração apenas na porta local `8089`; não criar rota administrativa pública no servidor de sites `8088`.

---

## 9. Arquitetura sugerida

Criar módulos pequenos, adaptados ao estilo do projeto:

### `site_monitor.py`

Responsável por:

- carregar as dez posições;
- validar que não há slugs duplicados ou inválidos;
- verificar os sites localmente e, quando configurado, publicamente;
- calcular os estados padronizados;
- gerar o resumo agregado;
- persistir os resultados da rodada;
- agendar a verificação a cada 30 minutos;
- expor uma interface Python simples para consulta pelo painel.

### `whatsapp_notifier.py`

Responsável por:

- validar configuração e credenciais;
- montar o payload conforme a API oficial e o template aprovado;
- enviar uma mensagem;
- usar timeout;
- interpretar resposta/erro sem revelar segredo;
- devolver um resultado estruturado ao chamador.

### Persistência

Sugestão de diretório privado: `data/monitoring/`, com permissões restritas no Termux.

Arquivos sugeridos:

- `sites.json`: mapeamento das dez posições.
- `last_report.json`: último estado completo conhecido.
- `history.jsonl`: histórico limitado das rodadas.
- `delivery-status.json`: último estado de envio e identificador da mensagem, sem token.

Evitar gravar a cada segundo; gravar por rodada, com escrita atômica (arquivo temporário + substituição) para reduzir risco de corrupção.

### Integração com `cdbs.py`

- Iniciar o monitor junto com o serviço principal.
- Encerrar o monitor na rotina de shutdown.
- Adicionar endpoints apenas na API administrativa já autenticada.
- Não bloquear os handlers HTTP durante verificações nem durante envio ao WhatsApp.
- Não reutilizar uma rota pública do site para alterar configuração.
- Proteger operações de configuração contra CSRF/uso sem sessão, seguindo o mecanismo já utilizado pelo projeto.

### Integração com `web/index.html`, `web/app.js` e `web/app.css`

- Criar uma seção de monitoramento consistente com o estilo atual.
- Buscar status por endpoint autenticado.
- Renderizar dados como texto seguro, sem inserir conteúdo do nome do site como HTML.
- Atualizar o estado sem recarregar a página toda.
- Exibir estados de carregamento, vazio, falha e sucesso.

### Integração com publicação/remoção

- Depois de publicação concluída, atualizar o mapa de sites e o monitor.
- Depois de remoção concluída, liberar a posição correspondente sem reordenar as outras.
- Se a operação principal falhar, não reportar sucesso.
- O envio de WhatsApp é secundário: uma falha nele não deve desfazer publicação, remoção ou backup.

---

## 10. Segurança e confiabilidade

- O painel administrativo continua restrito a `127.0.0.1:8089`.
- O túnel Cloudflare continua apontando apenas para a porta pública de sites, normalmente `8088`.
- Não criar endpoint público que exponha relatório administrativo, logs ou configuração.
- Não aceitar comandos remotos pelo WhatsApp na primeira versão; o canal é somente de saída.
- Não enviar conteúdo de arquivos, senhas de clientes, cookies, tokens, caminhos privados ou stack traces.
- Validar nomes de sites usando a mesma regra segura de slug existente.
- Limitar o tamanho do histórico e proteger permissões dos arquivos locais.
- Proteger contra envio duplicado se uma rodada for retomada após erro.
- A falha de um serviço externo não pode derrubar o CDBS.OS.
- O teste público, se ativado, só deve consultar URLs conhecidas e pertencentes às posições configuradas; não permitir que a interface aceite URLs arbitrárias que possam virar vetor SSRF.
- Não prometer que o monitor local consegue avisar sobre a própria queda.

---

## 11. Testes obrigatórios

### Testes do mapeamento

- Exatamente 10 posições no relatório.
- Posições vagas aparecem como `Vago`.
- Site cadastrado mas inacessível aparece como `Fora do ar` ou `Verificação inconclusiva`, nunca `Vago`.
- Slugs duplicados são recusados.
- Excluir um site libera sua posição sem renumerar as demais.
- Mais de 10 sites geram aviso claro.

### Testes de disponibilidade

- Site local com HTTP 200 e `index.html` válido.
- Site com HTTP 404/500.
- Timeout e conexão recusada.
- Falha de internet/DNS.
- Um site falha e os outros nove ainda são verificados.
- Modo público desativado não afirma que o acesso externo foi validado.
- Quick Tunnel alterado/ausente é tratado sem falsos positivos.
- Tempo total e timeouts não travam a API nem a interface.

### Testes de agendamento

- Intervalo padrão de 30 minutos.
- Não existem rodadas concorrentes.
- Erro no envio não interrompe a próxima rodada.
- Encerramento controlado interrompe o agendador.
- Falha de um ciclo não elimina o histórico anterior.

### Testes do WhatsApp

- Integração desativada.
- Credenciais ausentes/inválidas.
- Template ausente ou não aprovado.
- Resposta de API bem-sucedida.
- Timeout, erro HTTP, rate limit e ausência de rede.
- Nenhum token aparece em logs, interface, testes ou mensagens.
- Mensagem contém as dez posições e o resumo coerente.
- Destinatário está no formato internacional exigido.
- Envio de teste exige ação explícita do administrador.

### Regressão obrigatória

Executar a suíte atual de testes, especialmente `tests/test_cdbs.py`, `tests/test_backup.py`, `tests/test_catalog.py`, `tests/test_protecao.py`, `tests/test_recuperacao.py` e os demais testes existentes. Fazer teste manual no Termux e em navegador móvel.

---

## 12. Limites que devem ser documentados

- O agendador local depende de o processo CDBS.OS continuar em execução.
- Android pode encerrar ou suspender o Termux; `termux-wake-lock` e ajustes de bateria ajudam, mas não garantem disponibilidade contínua.
- Se o aparelho desligar, ficar sem internet ou o processo morrer, o monitor local não poderá enviar o relatório naquele horário.
- Para detectar o celular completamente offline e avisar por outro canal, será necessário um monitor externo independente, que fica fora do escopo mínimo desta primeira implementação.
- O Quick Tunnel é temporário e seu hostname pode mudar após reinicialização; não assumir domínio público fixo.
- A API oficial do WhatsApp exige configuração externa, credenciais válidas e possivelmente template aprovado para relatórios iniciados automaticamente. A aprovação e a configuração podem impedir o envio no mesmo dia; o código não deve fingir que a mensagem foi entregue.
- Uma resposta positiva da API pode significar que a mensagem foi aceita para processamento, não necessariamente que foi entregue ao destinatário. Exibir o estado real disponível na API.

---

## 13. Plano de implementação para publicar com segurança

### Fase A — monitor local

1. Implementar mapeamento fixo de dez posições.
2. Implementar verificação local com timeout e estados claros.
3. Persistir o último relatório e histórico limitado.
4. Adicionar seção Monitoramento ao painel.
5. Adicionar botão Verificar agora.
6. Criar testes automatizados para todos os estados.

### Fase B — WhatsApp oficial

1. Implementar adaptador oficial isolado.
2. Adicionar configuração por `.env`, desativada por padrão.
3. Preparar template de utilidade e documentar a configuração Meta.
4. Implementar envio de teste e registro de estado.
5. Só habilitar envio periódico quando configuração e template estiverem válidos.
6. Testar o relatório com os dez slots.

### Fase C — agendamento e lançamento

1. Ativar agendamento de 30 minutos.
2. Garantir que a rodada inicial não gere duas mensagens acidentais.
3. Executar toda a suíte de regressão.
4. Testar manualmente no Termux com o túnel ligado e desligado.
5. Verificar que a porta 8089 continua fora do túnel.
6. Atualizar README e documentação de instalação/atualização.
7. Fazer backup dos dados antes de instalar a atualização.

Se a integração WhatsApp ainda depender de aprovação externa, pode-se publicar o monitor local com o WhatsApp desativado, desde que isso esteja claramente indicado. Não anunciar o envio automático como funcional até uma mensagem real de teste ter sido recebida.

---

## 14. Critérios de aceite

- [ ] Painel exibe exatamente dez posições numeradas.
- [ ] Posições vazias aparecem como `Vago`.
- [ ] Sites indisponíveis não são classificados como vagos.
- [ ] A verificação não bloqueia o painel ou a publicação.
- [ ] O relatório de teste tem dez linhas e resumo correto.
- [ ] Agendamento usa intervalo de 30 minutos e evita concorrência.
- [ ] WhatsApp oficial é opcional e desativado até estar configurado.
- [ ] O estado do envio não é confundido com entrega confirmada.
- [ ] Tokens não aparecem em logs, UI, documentação ou ZIP.
- [ ] O painel administrativo não é exposto pelo Cloudflare Tunnel.
- [ ] Os testes existentes continuam passando.
- [ ] O processo de atualização preserva `.env`, sites, catálogos, senhas e backups.
- [ ] A documentação informa claramente os limites do Android e do Quick Tunnel.

## 15. Decisões recomendadas para esta versão

- Canal: WhatsApp Business Platform Cloud API oficial.
- Destinatário: número do proprietário configurado localmente, não fixado no código.
- Frequência: a cada 30 minutos.
- Cobertura: dez posições fixas.
- Conteúdo: relatório completo a cada ciclo, com estados e resumo.
- Estados: `Ativo`, `Fora do ar`, `Vago`, `Verificação inconclusiva`.
- Verificação: saúde local sempre; saúde pública apenas quando houver URL pública conhecida e configurada.
- Primeiro lançamento: sem comandos recebidos pelo WhatsApp; somente envio de relatórios.
- Monitor externo independente: fase futura.

## Autoridade de implementação para Claude (instrução obrigatória)

Este documento define **o resultado desejado, as regras de negócio e os critérios de segurança**. Claude tem autorização para modificar o código existente para implementar a especificação: pode criar, editar, reorganizar ou remover arquivos, alterar funções, rotas, interfaces, estruturas internas e arquitetura quando isso for tecnicamente necessário. **Não trate os exemplos de implementação ou nomes de arquivos deste documento como imutáveis.**

Antes de alterar, inspecione o projeto real e siga os padrões existentes quando forem adequados. Se a estrutura atual impedir uma implementação correta, adapte-a em vez de forçar uma solução incompatível. Preserve os requisitos essenciais deste documento, os dados do usuário, a autenticação, os sites publicados, catálogos, configurações, segredos e backups. Nunca afirme que algo está implementado ou testado sem realmente executar e verificar.

Claude deve implementar a funcionalidade de ponta a ponta (backend, interface, persistência, tratamento de erros e testes pertinentes), não apenas criar uma tela ou documentação. Pode corrigir defeitos existentes que bloqueiem a funcionalidade, desde que avalie regressões e acrescente testes. Se precisar divergir de um detalhe proposto, escolha a alternativa tecnicamente mais segura e registre a decisão, o motivo e os impactos no relatório final. Não remova funcionalidades existentes sem necessidade demonstrada.

**Critérios de conclusão:** código funcional integrado; testes automatizados executados e resultados informados; verificações manuais necessárias identificadas; limitações de ambiente/credenciais declaradas; instruções de instalação e recuperação atualizadas. Se não puder concluir uma etapa, declare-a explicitamente em vez de simular sucesso.



---

## Anexo 3 — ATUALIZACAO-CDBS-SISTEMA-NOTIFICACOES.md

# CDBS.OS — Atualização: notificações de estado dos sites via WhatsApp

**Objetivo:** preparar um sistema de monitoramento dos 10 espaços de hospedagem do CDBS.OS e enviar ao proprietário, pelo WhatsApp, um relatório completo a cada 30 minutos.

**Prioridade:** entregar com segurança na atualização planejada para 26/10/2026, sem comprometer publicação de sites, catálogos, autenticação ou backups. Se a integração oficial com WhatsApp não puder ser configurada e testada a tempo, publicar a parte local testada e manter o envio externo desativado até concluir a configuração — não substituir por automação não oficial frágil.

## 1. Comportamento desejado

O CDBS.OS terá exatamente **10 posições de hospedagem** no relatório. A cada 30 minutos, deve verificar e listar as 10 posições, incluindo as que ainda não foram usadas.

Exemplo de mensagem:

```text
CDBS.OS — Relatório de sites
26/10/2026 10:30

1. Ativo — loja-a
2. Ativo — portfolio
3. Fora do ar — catalogo-b
4. Vago
5. Vago
6. Ativo — site-pessoal
7. Vago
8. Fora do ar — teste
9. Vago
10. Vago

Resumo: 3 ativos · 2 fora do ar · 5 vagos
Última verificação: 10:30
```

Os nomes acima são apenas exemplos. O sistema deve usar os sites realmente cadastrados e não inventar nomes nem estados.

## 2. Estados dos 10 espaços

Cada posição deve apresentar exatamente um dos seguintes estados:

- **Ativo:** existe um site cadastrado e a verificação configurada foi bem-sucedida.
- **Fora do ar:** existe um site cadastrado, mas a verificação confirmou uma falha de disponibilidade após as tentativas definidas.
- **Vago:** não existe site atribuído à posição.
- **Verificação inconclusiva:** não foi possível concluir o teste (por exemplo, falha de rede/DNS/timeout). Não confundir automaticamente falha de verificação com site definitivamente fora do ar.

O relatório deve incluir a URL ou identificador do site quando disponível, sem expor URLs administrativas, tokens, senhas ou outros segredos. Deve também resumir a quantidade de ativos, fora do ar, vagos e verificações inconclusivas.

## 3. Frequência e lógica de monitoramento

- Executar o ciclo a cada **30 minutos** enquanto o serviço de monitoramento estiver ativo.
- Verificar todas as 10 posições em cada ciclo, inclusive as vagas.
- Usar timeout por site, limite de concorrência e tentativas curtas para evitar que um site lento bloqueie o relatório inteiro.
- Definir o que significa “ativo”: preferencialmente fazer uma requisição HTTP ao endereço público do site e validar uma resposta esperada; quando aplicável, comparar também com a disponibilidade local para distinguir falha do site de falha do túnel.
- Registrar horário, resultado, duração e motivo técnico resumido de cada verificação.
- Se uma verificação falhar, repetir de forma limitada antes de classificar o site como “Fora do ar”. Se o erro não permitir conclusão, usar “Verificação inconclusiva”.
- O relatório deve conter sempre as 10 posições, na ordem 1 a 10. Não omitir posições vagas nem sites com falha.
- Evitar relatórios duplicados para o mesmo ciclo. Registrar se a mensagem foi enviada ou se falhou.
- O envio do WhatsApp não pode bloquear nem interromper o servidor, as publicações, os catálogos ou os backups.

## 4. WhatsApp: integração e requisitos

Usar uma integração **oficial e suportada**, preferencialmente a WhatsApp Business Platform / Cloud API, ou um provedor oficial compatível.

Configuração necessária antes do envio real:

- Número de destino do proprietário, guardado em formato internacional e validado.
- Conta/aplicação e número de envio habilitados no provedor escolhido.
- Credenciais e permissões necessárias à API.
- Configuração de mensagem-modelo (template) aprovada quando exigida pelas regras do WhatsApp para mensagens iniciadas pelo sistema fora da janela de atendimento aplicável.
- Teste de entrega ao número do proprietário e tratamento de erros de API.

**Importante:** informar o número de destino, por si só, não cria um bot nem autoriza o envio. A integração oficial pode exigir configuração na Meta, verificação, credenciais e/ou custos conforme a solução escolhida. Não presumir que será possível enviar mensagens proativas a cada 30 minutos sem essa configuração.

Não usar bibliotecas que automatizam o WhatsApp Web por métodos não oficiais como solução de produção: podem quebrar, exigir sessão aberta e resultar em restrições ou bloqueio da conta.

Guardar tokens e segredos fora do código-fonte e do ZIP distribuído, em configuração local ignorada pelo Git, com permissões restritas. Nunca imprimir credenciais nos logs ou mostrá-las na interface depois de salvas.

## 5. Limite importante: o que acontece se o CDBS.OS cair?

Se o agendador e o envio do WhatsApp rodarem no mesmo Android/Termux que hospeda os sites, eles só podem enviar relatórios enquanto o aparelho, o processo e a conexão continuarem funcionando. Se o celular desligar, o Termux for encerrado ou a internet cair, o sistema local não conseguirá enviar o próprio aviso de indisponibilidade.

Para alertar também quando o aparelho/servidor inteiro ficar offline, será necessário um **monitor externo independente** que verifique os sites e envie o alerta por outra infraestrutura. Isso é diferente do relatório periódico gerado pelo CDBS.OS e deve ser documentado como fase separada.

## 6. Interface do CDBS.OS

Adicionar uma seção **Monitoramento** ou **Status dos sites** contendo:

- 10 posições fixas, numeradas de 1 a 10.
- Estado visual e textual: Ativo, Fora do ar, Vago ou Verificação inconclusiva.
- Nome/identificador do site e URL pública, quando aplicável.
- Última verificação e duração.
- Botão para executar verificação manual.
- Horário do próximo relatório e estado da integração WhatsApp: não configurada, pronta, enviada ou falhou.
- Campo para configurar o número de destino e controles para ativar/desativar relatórios.
- Pré-visualização da mensagem antes do primeiro envio e botão de teste, sem exibir credenciais.
- Histórico de verificações e de envios com mensagens de erro seguras.

A interface deve ser responsiva e utilizável em telas pequenas. Estados não podem ser comunicados somente por cores; usar rótulos e ícones acessíveis.

## 7. Central de notificações local

Além do WhatsApp, manter um histórico local de eventos importantes, incluindo:

- Relatórios de status gerados/enviados/falhos.
- Site publicado, atualizado ou removido.
- Backup concluído, atrasado ou com falha.
- Restauração concluída ou com falha.
- Espaço em disco baixo.
- Erros críticos do servidor e do túnel.

Persistir o evento localmente antes de tentar enviá-lo. Falha no WhatsApp não deve apagar o evento nem desfazer a operação original. Aplicar limite de histórico e retenção configurável.

## 8. Segurança e privacidade

- Não expor o painel administrativo nem criar uma rota administrativa pública para permitir monitoramento.
- Não incluir senhas, tokens, cookies, chaves de backup, conteúdo de arquivos ou dados privados de clientes nos relatórios.
- Validar o número de destino e restringir a configuração a uma sessão administrativa autenticada.
- Não aceitar comandos administrativos recebidos pelo WhatsApp nesta primeira versão.
- Aplicar timeout e limite de tentativas às requisições HTTP e à API de mensagens.
- Sanitizar mensagens de erro antes de mostrá-las ou enviá-las.
- Garantir que uma falha de API não gere uma sequência infinita de tentativas.

## 9. Testes obrigatórios

1. Existem sempre 10 posições no relatório.
2. Posição sem site cadastrado aparece como “Vago”.
3. Site com resposta válida aparece como “Ativo”.
4. Site indisponível após tentativas limitadas aparece como “Fora do ar”.
5. Falha de DNS, rede ou timeout não conclusiva aparece como “Verificação inconclusiva”.
6. Um site lento não impede a verificação dos outros nove.
7. O relatório mantém a ordem 1–10 e o resumo numérico corresponde aos estados listados.
8. A frequência é de 30 minutos, com horário de execução e fuso horário documentados.
9. O ciclo não envia duplicatas em caso de reinício ou execução concorrente.
10. WhatsApp não configurado não impede o funcionamento do CDBS.OS nem das verificações locais.
11. Falha, timeout ou limite de API do WhatsApp é registrado sem expor credenciais e sem travar o servidor.
12. Número de destino ausente/inválido é tratado com mensagem de configuração clara.
13. Tokens e segredos não aparecem no código, ZIP distribuído, logs ou interface.
14. A verificação pública não expõe o painel administrativo.
15. Executar testes de regressão de publicação, catálogo, autenticação, backup e restauração.
16. Testar manualmente no Termux/Android e confirmar recebimento real no WhatsApp antes de marcar a integração como pronta.

## 10. Plano de implementação

1. Inspecionar o código atual e identificar a fonte de verdade para os sites publicados e os 10 espaços.
2. Implementar a rotina de verificação de status com timeout, repetição limitada e resultados explícitos.
3. Implementar o relatório fixo com 10 posições e resumo.
4. Implementar agendamento de 30 minutos sem criar tarefas duplicadas após reinício.
5. Criar o histórico local e a seção responsiva de monitoramento.
6. Integrar a API oficial do WhatsApp atrás de uma interface de envio, com configuração segura e estado de integração claro.
7. Adicionar pré-visualização e mensagem de teste.
8. Executar os testes automatizados e a suíte de regressão existente.
9. Configurar as credenciais reais apenas no ambiente local do proprietário e validar uma entrega real.
10. Documentar instalação, configuração, custos/limites aplicáveis e o comportamento quando o celular fica offline.
11. Não declarar o envio automático de WhatsApp concluído até confirmar a entrega real. Se a configuração externa não ficar pronta no prazo, publicar apenas as partes locais comprovadamente testadas e deixar o canal WhatsApp desativado.

## 11. Critérios de aceite

- O painel apresenta exatamente 10 posições, com estado correto e última verificação.
- A cada 30 minutos, é gerado um relatório com todas as posições, inclusive vagas e indisponíveis.
- O relatório é enviado ao número configurado somente quando a integração oficial está pronta.
- Os totais do resumo coincidem com os 10 itens.
- Falhas de monitoramento ou de WhatsApp não interrompem o serviço nem causam perda de dados.
- Nenhuma credencial é incluída no código ou no pacote distribuído.
- A limitação de detecção de queda total do Android/Termux é explicada; monitoramento externo é tratado separadamente.
- Os testes de regressão críticos passam.

## 12. Decisões fixadas pelo proprietário

- Canal externo desejado: **WhatsApp**.
- Destinatário: número do proprietário, a ser configurado localmente; não registrar o número no documento ou no código.
- Frequência: **a cada 30 minutos**.
- Escopo: **todos os 10 espaços**, sempre na mesma mensagem.
- Estados mínimos: **Ativo**, **Fora do ar** e **Vago**; incluir **Verificação inconclusiva** para não confundir falhas de rede com indisponibilidade confirmada.

## 13. Recomendação de entrega

Priorizar primeiro a precisão do monitoramento e a mensagem com 10 posições. A integração oficial do WhatsApp depende de credenciais e configuração externa, portanto deve ser validada separadamente e não simulada. Manter a Central local útil mesmo antes de configurar o WhatsApp.

## Autoridade de implementação para Claude (instrução obrigatória)

Este documento define **o resultado desejado, as regras de negócio e os critérios de segurança**. Claude tem autorização para modificar o código existente para implementar a especificação: pode criar, editar, reorganizar ou remover arquivos, alterar funções, rotas, interfaces, estruturas internas e arquitetura quando isso for tecnicamente necessário. **Não trate os exemplos de implementação ou nomes de arquivos deste documento como imutáveis.**

Antes de alterar, inspecione o projeto real e siga os padrões existentes quando forem adequados. Se a estrutura atual impedir uma implementação correta, adapte-a em vez de forçar uma solução incompatível. Preserve os requisitos essenciais deste documento, os dados do usuário, a autenticação, os sites publicados, catálogos, configurações, segredos e backups. Nunca afirme que algo está implementado ou testado sem realmente executar e verificar.

Claude deve implementar a funcionalidade de ponta a ponta (backend, interface, persistência, tratamento de erros e testes pertinentes), não apenas criar uma tela ou documentação. Pode corrigir defeitos existentes que bloqueiem a funcionalidade, desde que avalie regressões e acrescente testes. Se precisar divergir de um detalhe proposto, escolha a alternativa tecnicamente mais segura e registre a decisão, o motivo e os impactos no relatório final. Não remova funcionalidades existentes sem necessidade demonstrada.

**Critérios de conclusão:** código funcional integrado; testes automatizados executados e resultados informados; verificações manuais necessárias identificadas; limitações de ambiente/credenciais declaradas; instruções de instalação e recuperação atualizadas. Se não puder concluir uma etapa, declare-a explicitamente em vez de simular sucesso.



---

## Anexo 4 — ATUALIZACAO-CDBSOS-ATUALIZADOR-PELO-PAINEL.md

# CDBS.OS — Especificação: atualização pelo painel com recuperação automática

**Objetivo:** permitir que o administrador envie um ZIP de uma nova versão pelo próprio painel CDBS.OS, valide o pacote, instale a versão e acompanhe o resultado sem executar comandos manualmente no Termux para cada atualização.

**Estado:** especificação para implementação. A presença deste arquivo no pacote não significa que a atualização pelo painel já exista ou tenha sido testada.

## 1. Experiência desejada

1. No painel administrativo, abrir **Sistema → Atualizações**.
2. Ver a versão instalada e o histórico de versões/atualizações.
3. Selecionar e enviar o ZIP pelo celular.
4. O servidor validar o arquivo e mostrar a versão detectada, os resultados da validação e os riscos relevantes.
5. O administrador confirmar explicitamente a instalação.
6. O painel mostrar as etapas: recebido, validando, backup, preparando, ativando, verificando e concluído/falhou/revertido.
7. Ao final, exibir a versão ativa, o log resumido e qualquer ação necessária.
8. Oferecer restauração manual de uma versão anterior quando houver uma cópia válida.

A interface deve ser simples, utilizável em tela pequena e não deve alegar sucesso antes de confirmar a saúde do serviço.

## 2. Inspecionar o projeto antes de implementar

Claude deve analisar a estrutura real do pacote, `cdbs.py`, `atualizar.sh`, `start.sh`, `start-cloudflare.sh`, as rotas existentes, a autenticação, o armazenamento e os testes. Adaptar a solução ao projeto real; não presumir nomes de arquivos, framework, caminhos ou processos sem verificar.

**Incompatibilidade já observada que precisa ser resolvida:** o ZIP existente contém os arquivos dentro de `cdbs_package/`, enquanto o `atualizar.sh` atual procura `cdbs.py` na raiz do ZIP. O fluxo de atualização deve reconhecer e validar o formato de pacote realmente adotado, de forma explícita e segura. Não afrouxar validações para simplesmente aceitar qualquer ZIP.

## 3. Arquitetura e ciclo de atualização

- Criar endpoints administrativos autenticados para consultar estado/histórico, enviar pacote, validar, iniciar instalação, acompanhar status e solicitar rollback. Os nomes exatos devem seguir as convenções reais do código.
- Limitar o tamanho do pacote; validar extensão e assinatura/estrutura ZIP; impedir path traversal, caminhos absolutos, links simbólicos, arquivos duplicados perigosos e escrita fora da pasta de staging.
- Exigir uma estrutura de pacote e metadados de versão documentados. Validar versão, arquivos obrigatórios, integridade, compatibilidade e requisitos antes de tocar na versão ativa.
- Preparar a nova versão numa pasta temporária/staging separada; nunca extrair diretamente por cima do código ativo.
- Fazer backup verificável dos arquivos de programa necessários para rollback e preservar os dados persistentes.
- Ativar a versão de forma atômica ou pelo mecanismo mais seguro suportado pelo sistema de arquivos/ambiente. Manter a versão anterior até a nova versão passar pelas verificações de saúde.
- Registrar cada etapa, horário, versão anterior/nova, resultado e erro útil, sem escrever senhas, tokens ou outros segredos nos logs.
- Impedir duas atualizações simultâneas usando bloqueio/mecanismo equivalente.
- Tratar falta de espaço, ZIP corrompido, interrupção, falha de permissão, falha de importação/sintaxe, servidor que não reinicia e timeout de health check.

## 4. O processo de atualização não pode depender apenas do servidor principal

Atualizar o código que atende a requisição pode encerrar o próprio processo HTTP. Implementar um coordenador/worker independente ou mecanismo equivalente que consiga continuar o trabalho após o processo principal parar. O desenho deve ser compatível com Android/Termux e com os scripts reais de inicialização.

Não prometer recuperação mágica caso o Android mate todos os processos ou o aparelho desligue. Nesse cenário, deixar um estado de atualização recuperável e fazer o mecanismo retomar ou restaurar a versão segura na próxima inicialização. Documentar qualquer passo que ainda precise de intervenção manual.

A atualização do painel e a reinicialização do servidor devem preservar a separação entre a porta administrativa local e a porta pública dos sites. Não expor o painel administrativo pelo Cloudflare Tunnel como efeito colateral.

## 5. Dados que não podem ser sobrescritos

Separar explicitamente código de dados persistentes. Preservar, conforme os caminhos reais do projeto:

- sites publicados e arquivos enviados pelos usuários;
- catálogos e dados de clientes;
- `.env`, senhas, chaves, tokens e configurações locais;
- backups existentes e metadados de recuperação;
- histórico de atualização necessário para rollback.

O pacote de versão não deve incluir segredos ou cópias de dados reais. Se a versão exigir migração de dados, criar migração versionada, validada e com estratégia de backup/recuperação antes de executar. Não afirmar que rollback é seguro para uma migração irreversível sem mecanismo comprovado.

## 6. Segurança do painel

- Usar a autenticação e proteção CSRF/origem adequadas ao padrão existente.
- Validar permissão administrativa em cada endpoint, não apenas esconder botões na interface.
- Não executar scripts ou comandos arbitrários recebidos no ZIP.
- Não aceitar atualização por URL remota sem requisito explícito e desenho seguro; o fluxo primário é upload local pelo painel.
- Não confiar apenas no nome do arquivo, extensão ou versão declarada no manifesto.
- Não registrar segredos nos logs nem retorná-los ao frontend.
- Mostrar confirmação antes da instalação e impedir instalação de pacote inválido.

## 7. Rollback e recuperação

- Manter ao menos a versão imediatamente anterior que passou por verificação, respeitando limites de armazenamento configuráveis.
- Não apagar automaticamente a última cópia válida antes de concluir e validar uma atualização nova.
- Se a nova versão não iniciar ou falhar nas verificações críticas, tentar restaurar a anterior e verificar a recuperação.
- Se a restauração automática falhar, manter a cópia de segurança e apresentar instruções de recuperação claras; não apagar dados nem declarar sucesso.
- Distinguir “código revertido”, “serviço recuperado” e “dados restaurados”: são estados diferentes.

## 8. Testes obrigatórios

Criar testes automatizados para, no mínimo:

1. pacote válido e versão válida;
2. arquivo que não é ZIP e ZIP corrompido;
3. pacote sem arquivos obrigatórios;
4. pacote com estrutura raiz antiga e estrutura atual aninhada, definindo claramente quais formatos são aceitos;
5. path traversal, caminhos absolutos, links simbólicos e nomes duplicados;
6. tentativa de incluir `.env`, segredos ou dados persistentes no pacote;
7. upload acima do limite;
8. falha de backup e falta de espaço simulada quando possível;
9. erro de sintaxe/importação da nova versão;
10. falha no health check e rollback;
11. interrupção/estado pendente e recuperação na próxima inicialização;
12. bloqueio de atualizações concorrentes;
13. acesso sem autenticação/permissão;
14. confirmação de que sites, catálogos, configurações, credenciais e backups não foram modificados por uma atualização de teste.

Executar a suíte disponível e informar os resultados reais. Testes simulados não substituem a validação final no Termux/Android.

## 9. Critérios de aceite

- É possível selecionar um ZIP pelo painel móvel e acompanhar o processo.
- Pacotes inválidos são recusados antes de alterar a versão ativa.
- Atualização bem-sucedida inicia e passa pelo health check.
- Falha de ativação aciona rollback ou deixa instruções recuperáveis, sem apagar a cópia anterior.
- Os dados persistentes e segredos são preservados.
- O painel administrativo continua local e não é exposto publicamente.
- Há histórico útil e logs sem segredos.
- A documentação explica instalação, compatibilidade, recuperação e limitações.

## 10. Autoridade de implementação para Claude (instrução obrigatória)

Este documento define o resultado desejado, as regras de negócio e os critérios de segurança; não é uma ordem para manter uma arquitetura inadequada. Claude tem autorização para modificar o código existente para implementar a funcionalidade: pode criar, editar, reorganizar ou remover arquivos, alterar funções, rotas, interfaces, scripts de inicialização, testes e arquitetura quando tecnicamente necessário. Os nomes de arquivos, endpoints e estruturas citados são propostas, salvo quando expressamente definidos como requisito funcional.

Claude deve inspecionar o projeto real antes de codificar, implementar a funcionalidade de ponta a ponta e adaptar as decisões ao ambiente Python/Android/Termux existente. Pode corrigir defeitos que bloqueiem a atualização, preservando as funcionalidades atuais. Não pode apagar ou sobrescrever dados persistentes para simplificar a implementação. Se precisar divergir de uma proposta, deve registrar o motivo e os impactos.

Não basta criar a tela ou este documento: é necessário implementar backend, worker/coordenador, validações, instalação, health check, rollback, interface, logs e testes aplicáveis. Não declarar implementação ou testes como concluídos sem executar e verificar. Ao final, apresentar arquivos alterados, testes executados com resultados, limitações restantes e instruções para testar no Termux.

