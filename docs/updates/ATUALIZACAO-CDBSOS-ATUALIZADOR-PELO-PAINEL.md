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
