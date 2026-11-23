import { useMutation, useQuery } from "@tanstack/react-query";
import { useState, type SubmitEvent } from "react";

import { api, unwrap, type Schemas } from "../../api/client";
import { ApiError, errorMessage } from "../../api/errors";
import { useInvalidate, useOrgKey } from "../../app/queries";
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
import { useNotify } from "../../components/notifications";
import { EmptyState, QueryView } from "../../components/states";
import { Badge, Button, DefinitionList, Section, Table } from "../../components/ui";
import { formatDate, formatDateTime } from "../../lib/format";
import {
  ANCHOR,
  DAY_COUNT,
  DEADLINE_KIND,
  DEADLINE_STATUS,
  RENEWAL_MODE,
  ROLL,
  enumValue,
  options,
} from "../../lib/labels";
import { useContractContext } from "./ContractLayout";

function useHolidayCalendars() {
  const key = useOrgKey();
  return useQuery({
    queryKey: key("holiday-calendars"),
    queryFn: () => unwrap(api.GET("/api/v1/holiday-calendars")),
    staleTime: 5 * 60_000,
  });
}

function DeadlineForm({ contractId }: { contractId: string }) {
  const notify = useNotify();
  const invalidate = useInvalidate();
  const calendars = useHolidayCalendars();
  const [anchor, setAnchor] = useState<Schemas["AnchorEnum"]>("effective_end");
  const create = useMutation({
    mutationFn: (body: Schemas["DeadlineCreateRequest"]) =>
      unwrap(
        api.POST("/api/v1/contracts/{contract_id}/deadlines", {
          params: { path: { contract_id: contractId } },
          body,
        }),
      ),
    onSuccess: async () => {
      notify.success("Prazo criado e alertas programados.");
      await invalidate("contracts", "deadlines", contractId);
      await invalidate("alerts");
    },
  });
  const errors = create.error instanceof ApiError ? create.error.fieldErrors : {};

  function handleSubmit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    const values = formText(event.currentTarget);
    const kind = enumValue(DEADLINE_KIND, values.kind);
    if (kind === undefined) return;
    create.mutate({
      kind,
      title: values.title ?? "",
      anchor,
      ...(anchor === "fixed"
        ? { fixed_date: values.fixed_date ?? null }
        : { offset_days: Number(values.offset_days ?? "0") }),
      day_count: enumValue(DAY_COUNT, values.day_count) ?? "calendar",
      roll: enumValue(ROLL, values.roll) ?? "none",
      holiday_calendar_id: values.holiday_calendar_id ?? null,
      notify_days_before: Number(values.notify_days_before ?? "0"),
    });
  }

  return (
    <form className="form form--wide" onSubmit={handleSubmit} noValidate>
      <FormError message={create.isError ? errorMessage(create.error) : null} />
      <div className="columns">
        <SelectField
          label="Tipo"
          name="kind"
          required
          options={options(DEADLINE_KIND)}
          error={errors.kind}
        />
        <TextField label="Título" name="title" required maxLength={200} error={errors.title} />
      </div>
      <div className="columns">
        <SelectField
          label="Referência"
          name="anchor"
          required
          value={anchor}
          onChange={(event) => {
            setAnchor(enumValue(ANCHOR, event.target.value) ?? "effective_end");
          }}
          options={options(ANCHOR)}
          error={errors.anchor}
        />
        {anchor === "fixed" ? (
          <TextField
            label="Data"
            name="fixed_date"
            type="date"
            required
            error={errors.fixed_date}
          />
        ) : (
          <TextField
            label="Deslocamento em dias"
            name="offset_days"
            type="number"
            required
            defaultValue={-30}
            hint="Negativo para antes da referência (ex.: −30 = 30 dias antes)."
            error={errors.offset_days}
          />
        )}
      </div>
      <div className="columns">
        <SelectField
          label="Contagem"
          name="day_count"
          defaultValue="calendar"
          options={options(DAY_COUNT)}
          error={errors.day_count}
        />
        <SelectField
          label="Se cair em dia não útil"
          name="roll"
          defaultValue="none"
          options={options(ROLL)}
          error={errors.roll}
        />
        <SelectField
          label="Calendário de feriados"
          name="holiday_calendar_id"
          placeholder="Calendário padrão da organização"
          options={(calendars.data ?? []).map((c) => ({ value: c.id, label: c.name }))}
          error={errors.holiday_calendar_id}
        />
      </div>
      <TextField
        label="Avisar com antecedência (dias)"
        name="notify_days_before"
        type="number"
        min={0}
        defaultValue={10}
        error={errors.notify_days_before}
      />
      <Button type="submit" busy={create.isPending}>
        Criar prazo
      </Button>
    </form>
  );
}

function Renewal({ contractId }: { contractId: string }) {
  const key = useOrgKey();
  const { can } = useSession();
  const notify = useNotify();
  const invalidate = useInvalidate();
  const [declining, setDeclining] = useState(false);
  const path = { params: { path: { contract_id: contractId } } };
  const policy = useQuery({
    queryKey: key("contracts", "renewal", contractId),
    queryFn: () => unwrap(api.GET("/api/v1/contracts/{contract_id}/renewal", path)),
  });
  const save = useMutation({
    mutationFn: (body: Schemas["RenewalPolicyRequest"]) =>
      unwrap(api.PUT("/api/v1/contracts/{contract_id}/renewal", { ...path, body })),
    onSuccess: async () => {
      notify.success("Política de renovação registrada.");
      await invalidate("contracts");
    },
  });
  const decline = useMutation({
    mutationFn: (reason: string) =>
      unwrap(
        api.POST("/api/v1/contracts/{contract_id}/renewal/decline", { ...path, body: { reason } }),
      ),
    onSuccess: async () => {
      setDeclining(false);
      notify.success("Não renovação registrada.");
      await invalidate("contracts");
    },
  });
  const errors = save.error instanceof ApiError ? save.error.fieldErrors : {};

  return (
    <Section title="Renovação">
      <QueryView query={policy}>
        {(data) => (
          <>
            <DefinitionList
              items={[
                ["Modo", RENEWAL_MODE[data.mode ?? "none"]],
                ["Prazo de cada renovação", data.term_months ? `${data.term_months} meses` : "—"],
                ["Máximo de renovações", data.max_renewals ?? "Sem limite"],
                ["Autorizada em", formatDateTime(data.authorized_at)],
                [
                  "Não renovação",
                  data.non_renewal_registered_at
                    ? `${formatDateTime(data.non_renewal_registered_at)} — ${data.non_renewal_reason}`
                    : "—",
                ],
              ]}
            />
            {can("contracts.lifecycle") && (
              <form
                className="form"
                noValidate
                key={`${data.mode ?? "none"}-${String(data.authorized_at)}`}
                onSubmit={(event) => {
                  event.preventDefault();
                  const values = formText(event.currentTarget);
                  save.mutate({
                    mode: enumValue(RENEWAL_MODE, values.mode) ?? "none",
                    term_months: Number(values.term_months ?? "12"),
                    max_renewals: values.max_renewals ? Number(values.max_renewals) : null,
                  });
                }}
              >
                <FormError message={save.isError ? errorMessage(save.error) : null} />
                <SelectField
                  label="Modo"
                  name="mode"
                  defaultValue={data.mode ?? "none"}
                  options={options(RENEWAL_MODE)}
                  hint="A renovação automática só acontece se estiver autorizada aqui e antes do fim da vigência."
                  error={errors.mode}
                />
                <TextField
                  label="Prazo de cada renovação (meses)"
                  name="term_months"
                  type="number"
                  min={1}
                  defaultValue={data.term_months ?? 12}
                  error={errors.term_months}
                />
                <TextField
                  label="Máximo de renovações"
                  name="max_renewals"
                  type="number"
                  min={1}
                  defaultValue={data.max_renewals ?? ""}
                  error={errors.max_renewals}
                />
                <Button type="submit" busy={save.isPending}>
                  Salvar política
                </Button>
              </form>
            )}
            {can("contracts.lifecycle") && !data.non_renewal_registered_at && (
              <Button
                variant="secondary"
                onClick={() => {
                  setDeclining(true);
                }}
              >
                Registrar não renovação
              </Button>
            )}
            <Dialog
              open={declining}
              title="Registrar não renovação"
              submitLabel="Registrar"
              submitVariant="danger"
              busy={decline.isPending}
              onClose={() => {
                setDeclining(false);
              }}
              onSubmit={(form) => {
                decline.mutate(textOf(form, "reason"));
              }}
            >
              <FormError message={decline.isError ? errorMessage(decline.error) : null} />
              <TextAreaField label="Motivo" name="reason" required rows={3} />
            </Dialog>
          </>
        )}
      </QueryView>
    </Section>
  );
}

export function DeadlinesTab() {
  const contract = useContractContext();
  const key = useOrgKey();
  const { can } = useSession();
  const notify = useNotify();
  const invalidate = useInvalidate();
  const deadlines = useQuery({
    queryKey: key("contracts", "deadlines", contract.id),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/contracts/{contract_id}/deadlines", {
          params: { path: { contract_id: contract.id } },
        }),
      ),
  });
  const complete = useMutation({
    mutationFn: (deadlineId: string) =>
      unwrap(
        api.POST("/api/v1/deadlines/{deadline_id}/complete", {
          params: { path: { deadline_id: deadlineId } },
        }),
      ),
    onSuccess: async () => {
      notify.success("Prazo concluído; alertas pendentes cancelados.");
      await invalidate("contracts", "deadlines", contract.id);
    },
  });

  return (
    <>
      <Section title="Prazos">
        <QueryView query={deadlines}>
          {(items) =>
            items.length === 0 ? (
              <EmptyState title="Nenhum prazo cadastrado." />
            ) : (
              <Table caption="Prazos do contrato">
                <thead>
                  <tr>
                    <th scope="col">Prazo</th>
                    <th scope="col">Tipo</th>
                    <th scope="col">Regra</th>
                    <th scope="col">Data</th>
                    <th scope="col">Situação</th>
                    <th scope="col">
                      <span className="visually-hidden">Ações</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((deadline) => (
                    <tr key={deadline.id}>
                      <td>{deadline.title}</td>
                      <td>{DEADLINE_KIND[deadline.kind]}</td>
                      <td>
                        {deadline.anchor === "fixed"
                          ? "Data fixa"
                          : `${String(deadline.offset_days ?? 0)} ${DAY_COUNT[deadline.day_count ?? "calendar"].toLowerCase()} de ${ANCHOR[deadline.anchor].toLowerCase()}`}
                      </td>
                      <td>{formatDate(deadline.due_date)}</td>
                      <td>
                        <Badge tone={deadline.status === "done" ? "success" : "neutral"}>
                          {DEADLINE_STATUS[deadline.status]}
                        </Badge>
                      </td>
                      <td>
                        {deadline.status === "pending" && can("obligations.manage") && (
                          <Button
                            variant="link"
                            busy={complete.isPending && complete.variables === deadline.id}
                            onClick={() => {
                              complete.mutate(deadline.id);
                            }}
                          >
                            Concluir
                          </Button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </Table>
            )
          }
        </QueryView>
        <FormError message={complete.isError ? errorMessage(complete.error) : null} />
      </Section>
      {can("obligations.manage") && (
        <Section title="Novo prazo">
          <DeadlineForm contractId={contract.id} />
        </Section>
      )}
      <Renewal contractId={contract.id} />
    </>
  );
}
