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

