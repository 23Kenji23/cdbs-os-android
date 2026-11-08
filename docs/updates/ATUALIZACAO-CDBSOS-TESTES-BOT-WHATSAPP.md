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
