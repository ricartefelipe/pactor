# Changelog

Formato baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/) e
[versionamento semântico](https://semver.org/lang/pt-BR/).

## [Não lançado]

### Adicionado

- QR code na ativação da verificação em duas etapas, gerado no navegador como SVG acessível
  (a chave em texto continua disponível).

## [0.1.0] - 2026-11-23

Primeira versão: plataforma completa de gestão do ciclo de vida de contratos,
com API, aplicação web, workers, agendador e roteiro demonstrativo comprovado
no navegador.

### Corrigido

- Revisão reenviada para aprovação depois de mudanças nos dados ou anexos do contrato: a regra
  do workflow era escolhida pelos dados antigos. Agora é preciso publicar uma nova revisão.
- Aditivo assinado que prorrogava o término não chegava ao encerramento, aos prazos nem às
  renovações.
- Renovações mensais encurtavam a vigência a cada ciclo (31/01 → 28/02 → 28/03).
- Integrações com o escopo `requests.create` não conseguiam criar solicitações; cada
  integração tem agora um usuário de serviço como autor.
- Solicitação de quem saiu da organização não podia ser convertida em contrato.
- Reajuste repetido com o mesmo índice, valores fora do limite (ex.: `1e30`), reajustes acima
  do máximo, parcelas simultâneas no mesmo contrato e vencimentos além do ano 9999 geravam
  efeito duplicado ou erro 500.
- Filtros de término da pesquisa combinavam término previsto e efetivo.
- Agendamento periódico do ciclo de vigência ausente; agora coberto por teste de guarda.
- Backup do banco impossível com RLS forçada; papel `pactor_backup` dedicado ao `pg_dump`.
- Esquema OpenAPI divergente das respostas reais: objetos sem descrição, listas
  declaradas como paginadas, filtros e `Idempotency-Key` ausentes, corpo de criação
  de modelos e solicitações e revisão atual do contrato declarada obrigatória.

### Adicionado

- Fundação do projeto: Django 5.2 LTS, DRF, Celery/RabbitMQ, Redis e PostgreSQL 17.
- Papéis de banco separados para migrations e aplicação.
- Logs estruturados em JSON com correlação e redação de segredos.
- Endpoints de liveness e readiness; métricas HTTP com cardinalidade limitada.
- Formato único de erros da API.
- Docker Compose com aplicação, workers, agendador, PostgreSQL, RabbitMQ, Redis, Mailpit e ClamAV.
- Organizações, departamentos, equipes, associações com papéis, convites por e-mail, troca de
  organização com rotação de sessão e revogação de acesso com efeito imediato.
- Políticas de autorização puras com matriz de papéis, visibilidade por classificação e MFA
  obrigatório para permissões privilegiadas.
- Row-Level Security forçada, FKs compostas por organização e contexto de tenant local à transação.
- Planos com trial e limites de usuários, contratos ativos, armazenamento e módulos.
- Auditoria somente-inserção com redação de dados sensíveis.
- Transactional Outbox com relay recuperável, consumidores deduplicados e registro de falhas.
- Idempotência persistida por chave, escopo e fingerprint.
- Contrapartes (PF/PJ) com validação de CPF/CNPJ, contatos, endereços e representantes.
- Contratos com partes e papéis, classificação de confidencialidade, referência sequencial,
  rascunho com concorrência otimista, revisões imutáveis com fingerprint, comparação textual,
  comentários por linha e compartilhamento com usuários, equipes e participantes externos.
- Visibilidade de contratos aplicada em consulta, equivalente à política (teste de propriedade).
- Workflows de aprovação versionados com condições validadas (sem `eval`), etapas sequenciais e
  paralelas, quórum, rejeição, solicitação de ajustes, delegação com prazo sem dupla contagem,
  proibição de autoaprovação, bloqueio por perda de acesso e substituição auditada de aprovador.
- Aprovação vinculada à revisão e ao fingerprint; nova revisão substitui a rodada aberta ou
  invalida a aprovação concedida.
- Assinaturas eletrônicas: porta de integração, simulador determinístico e adaptador Docusign
  (demonstração), envio da revisão aprovada com hash, ordem de assinatura, situação por
  participante, webhooks com HMAC idempotentes e tolerantes a desordem, recuperação de timeout
  por referência, cancelamento, documentos enviados e retornados preservados com hash e
  reconciliação periódica.
- Modelos de contrato versionados como dados controlados (campos tipados, seções opcionais,
  marcadores restritos a variáveis declaradas) e geração de revisões com registro da versão
  do modelo e dos dados usados.
- Solicitações de contrato com solicitante, departamento, tipo, contraparte, objetivo, valor
  estimado, datas, responsável, prioridade, histórico e conversão em contrato.
- Vigência separada de elaboração e assinatura (início futuro, prazo indeterminado, suspensão
  conforme política, rescisão registrada, encerramento automático e arquivamento), com
  histórico e agendador idempotente.
- Aditivos que preservam o original e resolução das condições vigentes em uma data.
- Limite de contratos ativos do plano aplicado no envio para assinatura.
- Pipeline de arquivos PDF/DOCX com quarentena, ClamAV real, inspeção em subprocesso isolado
  com limites de recursos (macros, referências externas, caminhos inválidos, bombas de
  compressão, conteúdo ativo), extração de texto, prévia, liberação e downloads autorizados.
- Anexos de contrato incluídos no fingerprint da revisão.
- Endpoint `/metrics` protegido por token, com métricas de outbox, consumidores, assinaturas,
  arquivos, envelopes, alertas e consultas por requisição, agregadas entre workers do Gunicorn.
- Reprocessamento autorizado e auditado de falhas de consumidores e de eventos esgotados.
- Comando `seed_demo` idempotente com duas organizações fictícias.
- Aplicação web em React e TypeScript, servida na mesma origem da API, com
  todas as áreas: login com segundo fator, troca de organização, painel,
  contrapartes, solicitações, modelos, contratos (rascunho, revisões, comparação,
  comentários, aprovação, assinatura com simulador, vigência, prazos, renovação,
  obrigações, valores, anexos e acessos), aprovações e delegações, calendário,
  pesquisa, relatórios, usuários e convites, estrutura, workflows, integrações,
  operação e auditoria.
- Tipos do frontend gerados a partir do OpenAPI e verificados na CI.
- Roteiro demonstrativo de 15 passos no navegador (Playwright) contra a pilha
  completa, com verificação de acessibilidade (axe-core).
- Runbooks com backup e restauração do banco e dos documentos exercitados, e roteiro demonstrativo.
- Prazos com regra explícita (dias corridos/úteis, feriados configurados, ajuste, fim de mês,
  bissextos), renovação automática somente autorizada e registrada, alertas idempotentes e
  recuperáveis no fuso da organização, e calendário consolidado.
- Obrigações recorrentes com ocorrências estáveis, evidência obrigatória, dependências,
  aprovação de conclusão e escalonamento.
- Valores e compromissos: parcelas com rateio exato, marcos, confirmações imutáveis, índices com
  período e origem registrados, reajustes que preservam valores confirmados e resumo que distingue
  valor contratado, projeção, obrigação e confirmação.
- Pesquisa full-text com autorização antes de resultados, snippets, contagens, facetas e
  sugestões; indexação por revisão que ignora jobs atrasados; painel e exportação CSV.
- Integrações OAuth (client credentials) vinculadas à organização, com papéis operacionais,
  escopos que restringem permissões, token apenas no cabeçalho e revogação imediata.
- Validação do esquema OpenAPI sem avisos como gate da CI.
- Mutation testing do núcleo de regras (manual e semanal).
- Autenticação por e-mail com confirmação e MFA (django-allauth headless).
- Pipeline de CI com lockfile, Ruff, mypy, pip-audit, Bandit, gitleaks, testes, gates de cobertura e análise da imagem.

[Não lançado]: https://github.com/ricartefelipe/pactor/compare/v0.1.0...develop
[0.1.0]: https://github.com/ricartefelipe/pactor/releases/tag/v0.1.0
