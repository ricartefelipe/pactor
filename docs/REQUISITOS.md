# Requisitos e critérios de aceite

Cada requisito possui identificador estável. A situação de cada item fica em
[`STATUS.md`](STATUS.md); este documento descreve o comportamento esperado.

O Pactor organiza informações e processos contratuais. Ele não interpreta
cláusulas, não emite parecer jurídico e não garante validade ou conformidade
de documentos pelo simples uso da plataforma.

## RN — Produto e modelo comercial

| ID | Requisito | Critério de aceite |
| --- | --- | --- |
| RN-01 | Planos configuráveis com limites de usuários, contratos ativos, armazenamento e módulos. | Ação que excede o limite é rejeitada com `plan_limit_exceeded` antes de qualquer efeito. |
| RN-02 | Trial com data de término. | Após o término sem plano ativo, operações de escrita são bloqueadas; leitura e exportação permanecem. |
| RN-03 | Assinatura da organização no Pactor separada dos contratos administrados. | Modelos e telas distintos; nenhum vínculo entre a assinatura da plataforma e contratos de clientes. |
| RN-04 | Valores comerciais são hipóteses de produto. | Planos de demonstração identificados como tal no seed. |

## RT — Multitenancy, identidade e permissões

| ID | Requisito | Critério de aceite |
| --- | --- | --- |
| RT-01 | Cada organização é um tenant isolado. | Consultas sem contexto de tenant retornam zero linhas (RLS); inserções com tenant divergente falham. |
| RT-02 | Departamentos, equipes, convites, associações, alternância e revogação. | Revogar associação encerra o acesso no próximo request, inclusive com sessão ativa. |
| RT-03 | Papéis: proprietário, administrador, gestor de contratos, solicitante, revisor, aprovador, financeiro, auditor. | Matriz de permissões em `ARQUITETURA.md` reproduzida por testes de policy. |
| RT-04 | Acesso por contrato, departamento ou equipe combinado com papel. | Contrato confidencial só é visível a quem tem concessão explícita, é responsável ou participa da rodada de aprovação. |
| RT-05 | Gerenciar usuários não concede acesso a confidenciais. | Administrador sem concessão não lista, pesquisa, conta nem exporta contratos confidenciais. |
| RT-06 | Participantes externos com acesso restrito. | Externo vê apenas registros compartilhados explicitamente, somente leitura. |
| RT-07 | Defesa em profundidade. | Escopo explícito nas consultas, policies, FKs compostas com `organization_id`, RLS forçada e papel de aplicação sem privilégios especiais. |
| RT-08 | Contexto de tenant local à transação. | `set_config(..., true)`; após commit ou rollback a conexão não mantém tenant. Tasks reconstituem o contexto. |

## RA — Autenticação, segurança e auditoria

| ID | Requisito | Critério de aceite |
| --- | --- | --- |
| RA-01 | Login por e-mail com confirmação, via django-allauth. | E-mail não confirmado não autentica. |
| RA-02 | MFA (TOTP) obrigatório para perfis privilegiados. | Proprietário/administrador sem MFA recebe `mfa_required` em operações administrativas. |
| RA-03 | Sessões no servidor e cookies seguros; CSRF em login e logout. | Requisição sem token CSRF é rejeitada; tokens nunca vão para `localStorage`. |
| RA-04 | Integrações máquina a máquina via OAuth (client credentials). | Cliente vinculado a uma organização; escopos limitam a API e não substituem autorização por contrato. |
| RA-05 | Rate limiting, proteção contra enumeração, política de senha, revogação de sessões, CSP. | Respostas de login e recuperação não revelam existência de e-mail. |
| RA-06 | Auditoria de operações relevantes. | Registro com ator, organização, recurso, ação, horário e alterações redigidas; tabela somente-inserção para o papel da aplicação. |
| RA-07 | Retenção e descarte configuráveis com bloqueio de exclusão. | Registros sob bloqueio não são descartados. |

## RC — Contrapartes, solicitações e modelos

| ID | Requisito | Critério de aceite |
| --- | --- | --- |
| RC-01 | Contrapartes PF/PJ com contatos, endereços, representantes e histórico. | Documento (CPF/CNPJ) validado por dígito verificador; único por organização. |
| RC-02 | Partes do contrato com papéis distintos. | Contrato registra parte contratante e contraparte com papel explícito. |
| RC-03 | Solicitações com solicitante, departamento, tipo, contraparte, objetivo, valor estimado, datas, responsável, prioridade, histórico. | Transições registradas; conversão em contrato preserva vínculo. |
| RC-04 | Tipos iniciais: prestação de serviços, fornecimento, confidencialidade, licenciamento, aditivo. | Tipos organizam o fluxo; nenhum texto jurídico é sugerido como adequado. |
| RC-05 | Modelos versionados com seções, campos, obrigatórios, blocos opcionais, variáveis permitidas e histórico. | Modelo publicado é imutável; nova edição cria nova versão. |
| RC-06 | Sem execução arbitrária em modelos. | Somente substituição `{{campo}}` de variáveis declaradas e blocos condicionados a booleanos declarados. |
| RC-07 | Geração registra versão do modelo, dados, documento, responsável e data. | Alterar o modelo não altera revisões já geradas. |

## RD — Revisões e integridade documental

| ID | Requisito | Critério de aceite |
| --- | --- | --- |
| RD-01 | Revisões com identidade própria, imutáveis após publicação. | Tentativa de alterar revisão publicada é rejeitada no serviço e no banco. |
| RD-02 | Registro de origem, conteúdo, anexos, metadados, autor, data, anterior e hash. | Hash SHA-256 do conteúdo canônico e de cada anexo. |
| RD-03 | Concorrência otimista. | Edição com versão desatualizada retorna `stale_version` sem sobrescrever. |
| RD-04 | Comentários e comparação textual entre revisões. | Diff textual por linhas; a interface não promete comparação semântica. |
| RD-05 | Hash não substitui assinatura digital. | Interface e documentação descrevem o hash apenas como identificação dos bytes. |

## RW — Aprovações

| ID | Requisito | Critério de aceite |
| --- | --- | --- |
| RW-01 | Regras por tipo, departamento, faixa de valor, classificação e características. | Condições com operadores e campos permitidos; avaliação determinística, sem `eval`. |
| RW-02 | Etapas sequenciais e paralelas, aprovadores elegíveis e quórum. | Etapa conclui quando o quórum é atingido; rejeição encerra a rodada. |
| RW-03 | Rejeição e solicitação de ajustes. | Ajuste devolve o contrato para elaboração e encerra a rodada. |
| RW-04 | Delegação com prazo, sem dupla contagem. | Um voto por aprovador original na etapa, seja direto ou por delegado. |
| RW-05 | Autoaprovação proibida quando configurada. | Autor da revisão ou solicitante não vota. |
| RW-06 | Perda de acesso de aprovador. | Votos pendentes de quem perdeu acesso deixam de contar para elegibilidade; etapa sem elegíveis suficientes fica bloqueada até reconfiguração. |
| RW-07 | Aprovação vinculada à revisão e ao conjunto submetido. | Rodada guarda revisão e fingerprint; nova revisão substitui a rodada aberta. |
| RW-08 | Workflow versionado. | Rodada registra a versão da definição utilizada. |
| RW-09 | Bloqueios impedem assinatura. | Envio exige rodada aprovada da mesma revisão e fingerprint idêntico. |

## RS — Assinaturas externas

| ID | Requisito | Critério de aceite |
| --- | --- | --- |
| RS-01 | Porta de integração com simulador local e adaptador Docusign (demo). | Simulador indisponível em produção. |
| RS-02 | Envio da revisão aprovada com participantes e ordem. | Envelope referencia revisão e hash do documento enviado. |
| RS-03 | Situação por participante distinta da conclusão do envelope. | Assinatura parcial não conclui o contrato. |
| RS-04 | Webhooks idempotentes, tolerantes a duplicidade e desordem. | Evento repetido não duplica efeitos; evento antigo não regride estado. |
| RS-05 | Eventos de envelopes antigos não ativam revisão posterior. | Envelope substituído ou anulado não altera o contrato. |
| RS-06 | Timeout após criação do envelope. | Recuperação consulta o provedor pela referência antes de qualquer novo envio. |
| RS-07 | Documentos enviados e retornados preservados separadamente com hash. | Ambos disponíveis para download autorizado. |

## RV — Vigência, aditivos e encerramento

| ID | Requisito | Critério de aceite |
| --- | --- | --- |
| RV-01 | Situações separadas de elaboração, assinatura e vigência. | Contrato assinado com início futuro permanece "aguardando vigência". |
| RV-02 | Prazo determinado ou indeterminado, suspensão, encerramento, rescisão, arquivamento. | Transições incompatíveis rejeitadas; ações exigem justificativa e geram histórico. |
| RV-03 | Aditivos preservam o original e registram alterações. | Condições vigentes em uma data resolvidas pela aplicação ordenada dos aditivos efetivos. |

## RO — Obrigações

| ID | Requisito | Critério de aceite |
| --- | --- | --- |
| RO-01 | Obrigações com responsável, parte, categoria, prazo, recorrência, dependências, evidências e situação. | Conclusão exige evidência quando configurada. |
| RO-02 | Ocorrências recorrentes estáveis. | Reprocessar o agendador não duplica ocorrências; mudanças não apagam concluídas. |
| RO-03 | Dependências e atrasos. | Ocorrência com dependência pendente não pode ser concluída; atraso calculado por data local. |

## RP — Prazos, renovação e alertas

| ID | Requisito | Critério de aceite |
| --- | --- | --- |
| RP-01 | Vencimentos, avisos, janelas de negociação, renovação, reajustes, lembretes, escalonamento. | Cada prazo vem de uma regra registrada. |
| RP-02 | Calendário explícito. | Inclusividade, dias corridos/úteis, feriados, fins de semana, bissextos e fim de mês definidos e testados. |
| RP-03 | Renovação automática somente autorizada. | Registro da base e da ocorrência; sem renovação implícita. |
| RP-04 | Alertas idempotentes e recuperáveis. | Após indisponibilidade, alertas vencidos são disparados uma única vez. |

## RF — Valores e compromissos

| ID | Requisito | Critério de aceite |
| --- | --- | --- |
| RF-01 | Valor contratado, parcelas/marcos, reajustes, aditivos financeiros, compromissos e confirmações. | Valor total, projeção, obrigação e confirmação são conceitos distintos. |
| RF-02 | `Decimal`, BRL explícito, `NUMERIC(14,2)`. | Valores não finitos e `float` rejeitados; arredondamento meio-par documentado. |
| RF-03 | Índices com valor, período e origem. | Nenhum índice oficial é inventado; dados demonstrativos identificados. |
| RF-04 | Confirmados não mudam retroativamente. | Confirmações são somente-inserção. |

## RQ — Arquivos

| ID | Requisito | Critério de aceite |
| --- | --- | --- |
| RQ-01 | PDF e DOCX com limites de tamanho, páginas e complexidade. | Arquivo fora dos limites é rejeitado com motivo registrado. |
| RQ-02 | Pipeline: recebimento, quarentena, validação, antimalware, extração, preview, liberação, falha. | Arquivo não liberado não é baixado, compartilhado nem indexado. |
| RQ-03 | Scanner real (ClamAV). | Scanner indisponível mantém o arquivo em quarentena. |
| RQ-04 | ZIP malformado, expansão excessiva, caminhos inválidos, macros, referências externas. | Cada caso rejeitado com motivo específico. |
| RQ-05 | Processamento isolado com limites de tempo, memória e disco. | Extração em subprocesso sem shell e com `setrlimit`. |

## RB — Pesquisa

| ID | Requisito | Critério de aceite |
| --- | --- | --- |
| RB-01 | Busca por identificador, contraparte, tipo, responsável, departamento, datas, situação, tags e conteúdo liberado. | Full-text PostgreSQL com índice GIN. |
| RB-02 | Autorização antes de resultados, snippets, contagens e facetas. | Usuário sem acesso não infere contratos confidenciais. |
| RB-03 | Indexação respeita a revisão. | Job atrasado de revisão anterior não sobrescreve índice mais novo. |
| RB-04 | Paginação limitada e ordenação determinística. | Página máxima 100; desempate por identificador. |

## RX — Processamento assíncrono

| ID | Requisito | Critério de aceite |
| --- | --- | --- |
| RX-01 | Transactional Outbox. | Falha entre commit e publicação é recuperada pelo relay periódico. |
| RX-02 | Identificadores estáveis, payload versionado, deduplicação, retentativas com backoff, registro de falhas, reprocessamento autorizado. | Consumidor processa cada evento uma vez por consumidor. |
| RX-03 | Tasks recebem identificadores e reconstituem tenant. | Worker reutilizado entre tenants não vaza contexto. |

## RI — API, interface e operação

| ID | Requisito | Critério de aceite |
| --- | --- | --- |
| RI-01 | API versionada (`/api/v1`) com OpenAPI, validação, paginação, filtros permitidos e erros estruturados. | Esquema gerado sem avisos. |
| RI-02 | Idempotência persistida para operações sensíveis. | Mesma chave e mesmo conteúdo devolvem o resultado original; conteúdo diferente gera `idempotency_conflict`. |
| RI-03 | Interface em português com as telas do produto consumindo dados reais. | Estados de carregamento, vazio, erro, validação e sucesso; troca de organização limpa caches. |
| RI-04 | Observabilidade. | Logs JSON correlacionados; métricas de latência, erros, filas, outbox, quarentena e integrações; liveness/readiness. |
