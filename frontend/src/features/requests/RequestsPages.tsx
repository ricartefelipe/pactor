import { useInfiniteQuery, useMutation, useQuery } from "@tanstack/react-query";
import { useState, type SubmitEvent } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router";

import { api, unwrap, type Schemas } from "../../api/client";
import { ApiError, errorMessage } from "../../api/errors";
import {
  cursorOf,
  useActiveCounterparties,
  useDepartments,
  useDirectory,
  useInvalidate,
  useMemberName,
  useOrgKey,
} from "../../app/queries";
import { useSession } from "../../auth/session";
import { Dialog } from "../../components/dialog";
import {
  FormError,
  SelectField,
  TextAreaField,
  TextField,
  formText,
  textOf,
} from "../../components/forms";
import { LoadMore } from "../../components/LoadMore";
import { useNotify } from "../../components/notifications";
import { EmptyState, QueryView, WhenReady } from "../../components/states";
import { Badge, Button, DefinitionList, PageHeader, Section, Table } from "../../components/ui";
import { decimalText, formatDate, formatDateTime, formatMoney } from "../../lib/format";
import {
  CONFIDENTIALITY,
  CONTRACT_TYPE,
  enumValue,
  labelOf,
  options,
  PARTY_ROLE,
  PRIORITY,
  REQUEST_STATUS,
} from "../../lib/labels";

type Status = Schemas["RequestStatusEnum"];

const STATUS_TONE: Readonly<Record<Status, "info" | "warning" | "success" | "neutral" | "danger">> =
  {
    submitted: "info",
    in_review: "warning",
    converted: "success",
    rejected: "danger",
    cancelled: "neutral",
  };

export function RequestsPage() {
  const key = useOrgKey();
  const { can } = useSession();
  const [params, setParams] = useSearchParams();
  const status = enumValue(REQUEST_STATUS, params.get("situacao") ?? undefined);
  const list = useInfiniteQuery({
    queryKey: key("requests", { status: status ?? null }),
    queryFn: ({ pageParam }) =>
      unwrap(
        api.GET("/api/v1/requests", {
          params: { query: { ...(status ? { status } : {}), cursor: pageParam } },
        }),
      ),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (page) => cursorOf(page.next),
  });

  return (
    <>
      <PageHeader
        title="Solicitações"
        description="Pedidos de novos contratos, da triagem à conversão."
        actions={
          can("requests.create") && (
            <Link className="button button--primary" to="/solicitacoes/nova">
              Nova solicitação
            </Link>
          )
        }
      />
      <div className="filters">
        <SelectField
          label="Situação"
          name="situacao"
          value={status ?? ""}
          placeholder="Todas"
          options={options(REQUEST_STATUS)}
          onChange={(event) => {
            setParams(event.target.value ? { situacao: event.target.value } : {});
          }}
        />
      </div>
      <QueryView query={list}>
        {(data) => {
          const rows = data.pages.flatMap((page) => page.results);
          if (rows.length === 0) return <EmptyState title="Nenhuma solicitação." />;
          return (
            <>
              <Table caption="Solicitações">
                <thead>
                  <tr>
                    <th scope="col">Referência</th>
                    <th scope="col">Título</th>
                    <th scope="col">Solicitante</th>
                    <th scope="col">Prioridade</th>
                    <th scope="col">Situação</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => (
                    <tr key={row.id}>
                      <td>{row.reference}</td>
                      <td>
                        <Link to={`/solicitacoes/${row.id}`}>{row.title}</Link>
                      </td>
                      <td>{row.requester_name}</td>
                      <td>{PRIORITY[row.priority]}</td>
                      <td>
                        <Badge tone={STATUS_TONE[row.status]}>{REQUEST_STATUS[row.status]}</Badge>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </Table>
              <LoadMore query={list} />
            </>
          );
        }}
      </QueryView>
    </>
  );
}

export function NewRequestPage() {
  const navigate = useNavigate();
  const notify = useNotify();
  const invalidate = useInvalidate();
  const counterparties = useActiveCounterparties();
  const departments = useDepartments();
  const create = useMutation({
    mutationFn: (body: Schemas["RequestCreateRequest"]) =>
      unwrap(api.POST("/api/v1/requests", { body })),
    onSuccess: async (created) => {
      notify.success(`Solicitação ${created.reference} enviada.`);
      await invalidate("requests");
      await navigate(`/solicitacoes/${created.id}`);
    },
  });
  const errors = create.error instanceof ApiError ? create.error.fieldErrors : {};

  function handleSubmit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    const values = formText(event.currentTarget);
    const contractType = enumValue(CONTRACT_TYPE, values.contract_type);
    const priority = enumValue(PRIORITY, values.priority);
    if (contractType === undefined || priority === undefined) return;
    create.mutate({
      title: values.title ?? "",
      objective: values.objective ?? "",
      contract_type: contractType,
      priority,
      counterparty_id: values.counterparty_id ?? null,
      department_id: values.department_id ?? null,
      estimated_value: values.estimated_value ? decimalText(values.estimated_value) : null,
      desired_start: values.desired_start ?? null,
      desired_end: values.desired_end ?? null,
    });
  }

  return (
    <>
      <PageHeader title="Nova solicitação" />
      <form className="form" onSubmit={handleSubmit} noValidate>
        <FormError message={create.isError ? errorMessage(create.error) : null} />
        <TextField label="Título" name="title" required maxLength={200} error={errors.title} />
        <SelectField
          label="Tipo de contrato"
          name="contract_type"
          required
          options={options(CONTRACT_TYPE)}
          error={errors.contract_type}
        />
        <TextAreaField
          label="Objetivo"
          name="objective"
          required
          rows={4}
          error={errors.objective}
        />
        <SelectField
          label="Contraparte"
          name="counterparty_id"
          placeholder="Ainda não definida"
          options={(counterparties.data?.results ?? []).map((c) => ({
            value: c.id,
            label: c.name,
          }))}
          error={errors.counterparty_id}
        />
        <SelectField
          label="Departamento"
          name="department_id"
          placeholder="Sem departamento"
          options={(departments.data ?? []).map((d) => ({ value: d.id, label: d.name }))}
          error={errors.department_id}
        />
        <TextField
          label="Valor estimado (R$)"
          name="estimated_value"
          inputMode="decimal"
          pattern="[0-9]+([.,][0-9]{1,2})?"
          hint="Sem separador de milhar, ex.: 1500,00"
          error={errors.estimated_value}
        />
        <TextField
          label="Início desejado"
          name="desired_start"
          type="date"
          error={errors.desired_start}
        />
        <TextField label="Fim desejado" name="desired_end" type="date" error={errors.desired_end} />
        <SelectField
          label="Prioridade"
          name="priority"
          required
          defaultValue="normal"
          options={options(PRIORITY)}
          error={errors.priority}
        />
        <Button type="submit" busy={create.isPending}>
          Enviar solicitação
        </Button>
      </form>
    </>
  );
}

type DialogKind = "reject" | "cancel" | "convert" | null;

function RequestActions({ request }: { request: Schemas["Request"] }) {
  const { can, me } = useSession();
  const notify = useNotify();
  const navigate = useNavigate();
  const invalidate = useInvalidate();
  const directory = useDirectory();
  const [dialog, setDialog] = useState<DialogKind>(null);
  const path = { params: { path: { id: request.id } } };

  const done = async (message: string) => {
    notify.success(message);
    setDialog(null);
    await invalidate("requests");
  };
  const review = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/requests/{id}/review", { ...path, body: {} })),
    onSuccess: () => done("Solicitação em triagem."),
  });
  const reject = useMutation({
    mutationFn: (reason: string) =>
      unwrap(api.POST("/api/v1/requests/{id}/reject", { ...path, body: { reason } })),
    onSuccess: () => done("Solicitação recusada."),
  });
  const cancel = useMutation({
    mutationFn: (reason: string) =>
      unwrap(api.POST("/api/v1/requests/{id}/cancel", { ...path, body: { reason } })),
    onSuccess: () => done("Solicitação cancelada."),
  });
  const convert = useMutation({
    mutationFn: (body: Schemas["ConvertRequest"]) =>
      unwrap(api.POST("/api/v1/requests/{id}/convert", { ...path, body })),
    onSuccess: async (contract) => {
      await done(`Contrato ${contract.reference} criado a partir da solicitação.`);
      await invalidate("contracts");
      await navigate(`/contratos/${contract.id}`);
    },
  });

  const open = request.status === "submitted" || request.status === "in_review";
  if (!open) return null;
  const manage = can("requests.manage");
  const mine = request.requester_id === me.id;

  return (
    <div className="actions">
      {manage && request.status === "submitted" && (
        <Button
          busy={review.isPending}
          onClick={() => {
            review.mutate();
          }}
        >
          Iniciar triagem
        </Button>
      )}
      {manage && (
        <Button
          onClick={() => {
            setDialog("convert");
          }}
        >
          Converter em contrato
        </Button>
      )}
      {manage && (
        <Button
          variant="secondary"
          onClick={() => {
            setDialog("reject");
          }}
        >
          Recusar
        </Button>
      )}
      {(manage || mine) && (
        <Button
          variant="secondary"
          onClick={() => {
            setDialog("cancel");
          }}
        >
          Cancelar solicitação
        </Button>
      )}
      <FormError message={review.isError ? errorMessage(review.error) : null} />
      <Dialog
        open={dialog === "reject" || dialog === "cancel"}
        title={dialog === "reject" ? "Recusar solicitação" : "Cancelar solicitação"}
        submitLabel={dialog === "reject" ? "Recusar" : "Cancelar solicitação"}
        submitVariant="danger"
        busy={reject.isPending || cancel.isPending}
        onClose={() => {
          setDialog(null);
        }}
        onSubmit={(form) => {
          const reason = textOf(form, "reason");
          if (dialog === "reject") reject.mutate(reason);
          else cancel.mutate(reason);
        }}
      >
        <FormError
          message={
            (reject.error ?? cancel.error) ? errorMessage(reject.error ?? cancel.error) : null
          }
        />
        <TextAreaField label="Motivo" name="reason" required rows={3} />
      </Dialog>
      <Dialog
        open={dialog === "convert"}
        title="Converter em contrato"
        submitLabel="Criar contrato"
        busy={convert.isPending}
        onClose={() => {
          setDialog(null);
        }}
        onSubmit={(form) => {
          const values = formText(form);
          convert.mutate({
            owner_id: values.owner_id ?? "",
            organization_role: enumValue(PARTY_ROLE, values.organization_role) ?? "contractor",
            counterparty_role: enumValue(PARTY_ROLE, values.counterparty_role) ?? "contracted",
            confidentiality: enumValue(CONFIDENTIALITY, values.confidentiality) ?? "internal",
          });
        }}
      >
        <FormError message={convert.isError ? errorMessage(convert.error) : null} />
        <WhenReady queries={[directory]}>
          {() => (
            <SelectField
              label="Responsável pelo contrato"
              name="owner_id"
              required
              defaultValue={me.id}
              options={(directory.data ?? []).map((m) => ({
                value: m.user_id,
                label: m.full_name || m.email,
              }))}
            />
          )}
        </WhenReady>
        <SelectField
          label="Papel da nossa organização"
          name="organization_role"
          required
          defaultValue="contractor"
          options={options(PARTY_ROLE)}
        />
        <SelectField
          label="Papel da contraparte"
          name="counterparty_role"
          required
          defaultValue="contracted"
          options={options(PARTY_ROLE)}
        />
        <SelectField
          label="Confidencialidade"
          name="confidentiality"
          required
          defaultValue="internal"
          options={options(CONFIDENTIALITY)}
        />
      </Dialog>
    </div>
  );
}

export function RequestPage() {
  const { id = "" } = useParams();
  const key = useOrgKey();
  const memberName = useMemberName();
  const detail = useQuery({
    queryKey: key("requests", "detail", id),
    queryFn: () => unwrap(api.GET("/api/v1/requests/{id}", { params: { path: { id } } })),
  });

  return (
    <QueryView query={detail}>
      {(request) => (
        <>
          <PageHeader
            title={`${request.reference} · ${request.title}`}
            description={
              <Badge tone={STATUS_TONE[request.status]}>{REQUEST_STATUS[request.status]}</Badge>
            }
            actions={<RequestActions request={request} />}
          />
          <DefinitionList
            items={[
              ["Tipo", CONTRACT_TYPE[request.contract_type]],
              ["Solicitante", request.requester_name],
              ["Contraparte", request.counterparty_name || "—"],
              ["Valor estimado", formatMoney(request.estimated_value)],
              [
                "Período desejado",
                `${formatDate(request.desired_start)} a ${formatDate(request.desired_end)}`,
              ],
              ["Prioridade", PRIORITY[request.priority]],
              ["Responsável pela triagem", memberName(request.assignee_id)],
              [
                "Contrato gerado",
                request.contract_id ? (
                  <Link to={`/contratos/${request.contract_id}`}>Abrir contrato</Link>
                ) : (
                  "—"
                ),
              ],
            ]}
          />
          <Section title="Objetivo">
            <p className="prose">{request.objective}</p>
          </Section>
          <Section title="Histórico">
            <ol className="timeline">
              {request.events.map((event) => (
                <li key={event.id}>
                  <strong>{labelOf(REQUEST_STATUS, event.to_status)}</strong> por{" "}
                  {memberName(event.actor_id)} em {formatDateTime(event.created_at)}
                  {event.note && <p className="muted">{event.note}</p>}
                </li>
              ))}
            </ol>
          </Section>
        </>
      )}
    </QueryView>
  );
}
