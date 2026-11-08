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

