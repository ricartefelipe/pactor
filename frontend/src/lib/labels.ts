/**
 * Rótulos em português para os enums da API. Os tipos `Record<Enum, string>`
 * garantem, em tempo de compilação, que todo valor novo da API tenha rótulo.
 */
import type { Schemas } from "../api/client";

type Labels<K extends string> = Readonly<Record<K, string>>;

export const CONTRACT_TYPE: Labels<Schemas["ContractTypeEnum"]> = {
  services: "Prestação de serviços",
  supply: "Fornecimento",
  nda: "Confidencialidade",
  licensing: "Licenciamento",
  amendment: "Aditivo",
};

export const DRAFTING_STATUS: Labels<Schemas["DraftingStatusEnum"]> = {
  draft: "Em elaboração",
  in_approval: "Em aprovação",
  approved: "Aprovado",
  signing: "Em assinatura",
  signed: "Assinado",
  cancelled: "Cancelado",
};

export const LIFECYCLE_STATUS: Labels<Schemas["LifecycleStatusEnum"]> = {
  not_started: "Não iniciado",
  awaiting_start: "Aguardando início",
  active: "Vigente",
  suspended: "Suspenso",
  ended: "Encerrado",
  terminated: "Rescindido",
  archived: "Arquivado",
};

export const CONFIDENTIALITY: Labels<Schemas["ConfidentialityEnum"]> = {
  internal: "Interno",
  restricted: "Restrito ao departamento",
  confidential: "Confidencial",
};

export const PARTY_ROLE: Labels<Schemas["PartyRoleEnum"]> = {
  contractor: "Contratante",
  contracted: "Contratada",
  licensor: "Licenciante",
  licensee: "Licenciada",
  disclosing: "Parte reveladora",
  receiving: "Parte receptora",
  mutual: "Mútua",
};

export const PARTY_KIND: Labels<Schemas["PartyKindEnum"]> = {
  company: "Pessoa jurídica",
  individual: "Pessoa física",
};

export const ROLE: Labels<Schemas["RoleEnum"]> = {
  owner: "Proprietário",
  admin: "Administrador",
  contract_manager: "Gestor de contratos",
  requester: "Solicitante",
  reviewer: "Revisor",
  approver: "Aprovador",
  finance: "Financeiro",
  auditor: "Auditor",
};

export const REQUEST_STATUS: Labels<Schemas["RequestStatusEnum"]> = {
  submitted: "Enviada",
  in_review: "Em triagem",
  rejected: "Recusada",
  cancelled: "Cancelada",
  converted: "Convertida em contrato",
};

export const PRIORITY: Labels<Schemas["PriorityEnum"]> = {
  low: "Baixa",
  normal: "Normal",
  high: "Alta",
  urgent: "Urgente",
};

export const ROUND_STATUS: Labels<Schemas["RoundStatusEnum"]> = {
  open: "Em andamento",
  approved: "Aprovada",
  rejected: "Rejeitada",
  changes_requested: "Ajustes solicitados",
  superseded: "Substituída por nova revisão",
};

export const STEP_STATE: Labels<Schemas["StepStateEnum"]> = {
  waiting: "Aguardando etapa anterior",
  active: "Em votação",
  approved: "Aprovada",
  blocked: "Bloqueada (aprovador sem acesso)",
};

export const DECISION: Labels<Schemas["DecisionEnum"]> = {
  approve: "Aprovar",
  reject: "Rejeitar",
  request_changes: "Solicitar ajustes",
};

export const ENVELOPE_STATE: Labels<Schemas["EnvelopeStateEnum"]> = {
  pending_dispatch: "Aguardando envio",
  dispatch_unknown: "Envio em verificação",
  sent: "Enviado",
  delivered: "Entregue",
  completed: "Concluído",
  declined: "Recusado",
  voiding: "Cancelando",
  voided: "Cancelado",
  failed: "Falhou",
};

export const RECIPIENT_STATUS: Labels<Schemas["RecipientStatusEnum"]> = {
  created: "Criado",
  sent: "Enviado",
  delivered: "Entregue",
  signed: "Assinou",
  declined: "Recusou",
};

export const DEADLINE_KIND: Labels<Schemas["DeadlineKindEnum"]> = {
  expiry: "Vencimento",
  termination_notice: "Aviso de não renovação",
  negotiation_window: "Janela de negociação",
  renewal_decision: "Decisão de renovação",
  adjustment: "Reajuste",
  reminder: "Lembrete",
};

export const DEADLINE_STATUS: Labels<Schemas["DeadlineStatusEnum"]> = {
  pending: "Pendente",
  done: "Concluído",
  cancelled: "Cancelado",
  unscheduled: "Sem data (aguarda vigência)",
};

export const ANCHOR: Labels<Schemas["AnchorEnum"]> = {
  effective_end: "Fim da vigência",
  effective_start: "Início da vigência",
  fixed: "Data fixa",
};

export const DAY_COUNT: Labels<Schemas["DayCountEnum"]> = {
  calendar: "Dias corridos",
  business: "Dias úteis",
};

export const ROLL: Labels<Schemas["RollEnum"]> = {
  none: "Sem ajuste",
  following: "Próximo dia útil",
  preceding: "Dia útil anterior",
};

export const ALERT_KIND: Labels<Schemas["AlertKindEnum"]> = {
  reminder: "Lembrete",
  due: "Vencimento",
  escalation: "Escalonamento",
};

export const ALERT_STATUS: Labels<Schemas["AlertStatusEnum"]> = {
  pending: "Agendado",
  sent: "Enviado",
  cancelled: "Cancelado",
};

export const OBLIGATION_CATEGORY: Labels<Schemas["CategoryEnum"]> = {
  report: "Relatório",
  certificate: "Certidão",
  documentation: "Documentação",
  review: "Revisão",
  milestone: "Marco",
  other: "Outra",
};

export const FREQUENCY: Labels<Schemas["FrequencyEnum"]> = {
  once: "Única",
  weekly: "Semanal",
  monthly: "Mensal",
  quarterly: "Trimestral",
  semiannual: "Semestral",
  yearly: "Anual",
};

export const RESPONSIBLE_PARTY: Labels<Schemas["ResponsiblePartyEnum"]> = {
  organization: "Nossa organização",
  counterparty: "Contraparte",
};

export const OCCURRENCE_STATUS: Labels<Schemas["OccurrenceStatusEnum"]> = {
  pending: "Pendente",
  submitted: "Aguardando aprovação",
  completed: "Concluída",
  cancelled: "Cancelada",
};

export const INSTALLMENT_STATUS: Labels<Schemas["InstallmentStatusEnum"]> = {
  planned: "Prevista",
  confirmed: "Confirmada",
  cancelled: "Cancelada",
};

export const INSTALLMENT_KIND: Labels<Schemas["InstallmentKindEnum"]> = {
  installment: "Parcela",
  milestone: "Marco",
};

export const RENEWAL_MODE: Labels<Schemas["ModeEnum"]> = {
  none: "Sem renovação",
  manual: "Renovação manual",
  automatic: "Renovação automática",
};

export const FILE_STATUS: Labels<Schemas["StoredFileStatusEnum"]> = {
  quarantined: "Em análise",
  released: "Liberado",
  rejected: "Recusado na validação",
  infected: "Bloqueado pelo antivírus",
};

export const MEMBERSHIP_STATUS: Labels<Schemas["MembershipStatusEnum"]> = {
  active: "Ativo",
  revoked: "Revogado",
};

export const MEMBERSHIP_KIND: Labels<Schemas["MembershipKindEnum"]> = {
  internal: "Interno",
  external: "Participante externo",
};

export const INVITATION_STATUS: Labels<Schemas["InvitationStatusEnum"]> = {
  pending: "Pendente",
  accepted: "Aceito",
  revoked: "Revogado",
};

export const FAILURE_STATUS: Labels<Schemas["FailureStatusEnum"]> = {
  retrying: "Em nova tentativa",
  failed: "Falhou",
  requeued: "Reenviada",
  resolved: "Resolvida",
};

export const SCOPE: Labels<Schemas["ScopesEnum"]> = {
  "contracts.read": "Ler contratos",
  "counterparties.read": "Ler contrapartes",
  "counterparties.write": "Cadastrar contrapartes",
  "requests.create": "Criar solicitações",
  "finance.read": "Ler valores",
  "reports.read": "Ler relatórios",
};

export const AMENDMENT_FIELD: Labels<Schemas["FieldEnum"]> = {
  total_value: "Valor total",
  effective_end: "Fim da vigência",
  description: "Descrição",
};

export const REVISION_SOURCE: Labels<Schemas["RevisionSourceEnum"]> = {
  manual: "Edição manual",
  template: "Gerada por modelo",
};

/** Opções para `<select>` a partir de um mapa de rótulos, na ordem declarada. */
export function options<K extends string>(labels: Labels<K>): { value: K; label: string }[] {
  return (Object.keys(labels) as K[]).map((value) => ({ value, label: labels[value] }));
}

/** Rótulo de um valor vindo como texto livre (ex.: chaves de contagem); cai no próprio valor. */
export function labelOf(labels: Readonly<Record<string, string>>, value: string): string {
  return Object.hasOwn(labels, value) ? (labels[value] ?? value) : value;
}

/**
 * Converte texto de um campo de formulário no valor do enum, validando contra
 * o mapa de rótulos (que contém exatamente os valores aceitos pela API).
 */
export function enumValue<K extends string>(
  labels: Labels<K>,
  raw: string | undefined,
): K | undefined {
  return raw !== undefined && Object.hasOwn(labels, raw) ? (raw as K) : undefined;
}
