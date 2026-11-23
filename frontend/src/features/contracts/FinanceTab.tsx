import { useMutation, useQuery } from "@tanstack/react-query";
import { useState, type SubmitEvent } from "react";

import { api, unwrap, type Schemas } from "../../api/client";
import { ApiError, errorMessage } from "../../api/errors";
import { useInvalidate, useOrgKey } from "../../app/queries";
import { useSession } from "../../auth/session";
import { Dialog } from "../../components/dialog";
import {
  CheckboxField,
  FormError,
  SelectField,
  TextField,
  formText,
  textOf,
} from "../../components/forms";
import { useNotify } from "../../components/notifications";
import { Restricted } from "../../components/Restricted";
import { EmptyState, QueryView } from "../../components/states";
import { Badge, Button, Section, Table } from "../../components/ui";
import { formatDate, formatMoney, todayIso } from "../../lib/format";
import {
  FREQUENCY,
  INSTALLMENT_KIND,
  INSTALLMENT_STATUS,
  enumValue,
  options,
} from "../../lib/labels";
import { useContractContext } from "./ContractLayout";

const MONEY_PATTERN = "[0-9]+([.][0-9]{1,2})?";

function ConfirmDialog({
  installment,
  onClose,
}: {
  installment: Schemas["Installment"];
  onClose: () => void;
}) {
  const notify = useNotify();
  const invalidate = useInvalidate();
  const confirm = useMutation({
    mutationFn: (body: Schemas["ConfirmRequest"]) =>
      unwrap(
        api.POST("/api/v1/installments/{installment_id}/confirm", {
          params: { path: { installment_id: installment.id } },
          body,
        }),
      ),
    onSuccess: async () => {
      notify.success("Pagamento confirmado.");
      onClose();
      await invalidate("contracts", "finance");
    },
  });
  const errors = confirm.error instanceof ApiError ? confirm.error.fieldErrors : {};
  return (
    <Dialog
      open
      title={`Confirmar parcela ${installment.sequence}`}
      submitLabel="Confirmar"
      busy={confirm.isPending}
      onClose={onClose}
      onSubmit={(form) => {
        confirm.mutate({
          amount: textOf(form, "amount"),
          confirmed_on: textOf(form, "confirmed_on"),
          reference: textOf(form, "reference"),
        });
      }}
    >
      <FormError message={confirm.isError ? errorMessage(confirm.error) : null} />
      <TextField
        label="Valor pago (R$)"
        name="amount"
        required
        inputMode="decimal"
        pattern={MONEY_PATTERN}
        defaultValue={installment.amount}
        error={errors.amount}
      />
      <TextField
        label="Data do pagamento"
        name="confirmed_on"
        type="date"
        required
        defaultValue={todayIso()}
        error={errors.confirmed_on}
      />
      <TextField
        label="Referência (nota, comprovante)"
        name="reference"
        required
        error={errors.reference}
      />
    </Dialog>
  );
}

function ScheduleForms({ contractId }: { contractId: string }) {
  const notify = useNotify();
  const invalidate = useInvalidate();
  const path = { params: { path: { contract_id: contractId } } };
  const schedule = useMutation({
    mutationFn: (body: Schemas["ScheduleRequest"]) =>
      unwrap(api.POST("/api/v1/contracts/{contract_id}/finance/installments", { ...path, body })),
    onSuccess: async (created) => {
      notify.success(`${created.length} parcelas criadas; o rateio fecha exatamente no total.`);
      await invalidate("contracts", "finance");
    },
  });
  const milestone = useMutation({
    mutationFn: (body: Schemas["MilestoneRequest"]) =>
      unwrap(api.POST("/api/v1/contracts/{contract_id}/finance/milestones", { ...path, body })),
    onSuccess: async () => {
      notify.success("Marco financeiro criado.");
      await invalidate("contracts", "finance");
    },
  });
  const scheduleErrors = schedule.error instanceof ApiError ? schedule.error.fieldErrors : {};
  const milestoneErrors = milestone.error instanceof ApiError ? milestone.error.fieldErrors : {};

  function handleSchedule(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    const values = formText(event.currentTarget);
    const frequency = enumValue(FREQUENCY, values.frequency);
    if (!frequency) return;
    schedule.mutate({
      total: values.total ?? "",
      parts: Number(values.parts ?? "1"),
      first_due: values.first_due ?? "",
      frequency,
      description: values.description ?? "",
    });
  }

  function handleMilestone(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    const values = formText(event.currentTarget);
    milestone.mutate({
      description: values.description ?? "",
      due_date: values.due_date ?? "",
      amount: values.amount ?? "",
    });
  }

  return (
    <div className="columns">
      <Section title="Gerar parcelas">
        <form className="form" onSubmit={handleSchedule} noValidate>
          <FormError message={schedule.isError ? errorMessage(schedule.error) : null} />
          <TextField
            label="Valor total (R$)"
            name="total"
            required
            inputMode="decimal"
            pattern={MONEY_PATTERN}
            error={scheduleErrors.total}
          />
          <TextField
            label="Número de parcelas"
            name="parts"
            type="number"
            min={1}
            required
            defaultValue={12}
            error={scheduleErrors.parts}
          />
          <TextField
            label="Primeiro vencimento"
            name="first_due"
            type="date"
            required
            error={scheduleErrors.first_due}
          />
          <SelectField
            label="Periodicidade"
            name="frequency"
            required
            defaultValue="monthly"
            options={options(FREQUENCY)}
            error={scheduleErrors.frequency}
          />
          <TextField label="Descrição" name="description" error={scheduleErrors.description} />
          <Button type="submit" busy={schedule.isPending}>
            Gerar parcelas
          </Button>
        </form>
      </Section>
      <Section title="Adicionar marco">
        <form className="form" onSubmit={handleMilestone} noValidate>
          <FormError message={milestone.isError ? errorMessage(milestone.error) : null} />
          <TextField
            label="Descrição"
            name="description"
            required
            error={milestoneErrors.description}
          />
          <TextField
            label="Vencimento"
            name="due_date"
            type="date"
            required
            error={milestoneErrors.due_date}
          />
          <TextField
            label="Valor (R$)"
            name="amount"
            required
            inputMode="decimal"
            pattern={MONEY_PATTERN}
            error={milestoneErrors.amount}
          />
          <Button type="submit" busy={milestone.isPending}>
            Adicionar marco
          </Button>
        </form>
      </Section>
    </div>
  );
}

/** Valor de índice informado pela organização, com fonte e período (nada é embutido). */
function IndexValueForm() {
  const notify = useNotify();
  const invalidate = useInvalidate();
  const register = useMutation({
    mutationFn: (body: Schemas["IndexValueCreateRequest"]) =>
      unwrap(api.POST("/api/v1/indexes", { body })),
    onSuccess: async (value) => {
      notify.success(`Índice ${value.series_name} registrado para o período.`);
      await invalidate("indexes");
    },
  });
  const errors = register.error instanceof ApiError ? register.error.fieldErrors : {};

  function handleSubmit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const values = formText(form);
    register.mutate(
      {
        series_name: values.series_name ?? "",
        index_source: values.index_source ?? "",
        period: values.period ?? "",
        rate_percent: values.rate_percent ?? "",
        source_note: values.source_note ?? "",
        is_demonstrative: new FormData(form).get("is_demonstrative") === "on",
      },
      {
        onSuccess: () => {
          form.reset();
        },
      },
    );
  }

  return (
    <form className="form" onSubmit={handleSubmit} noValidate>
      <h3>Registrar valor de índice</h3>
      <FormError message={register.isError ? errorMessage(register.error) : null} />
      <TextField
        label="Série"
        name="series_name"
        required
        maxLength={80}
        hint="Ex.: IPCA"
        error={errors.series_name}
      />
      <TextField
        label="Fonte"
        name="index_source"
        required
        maxLength={200}
        error={errors.index_source}
      />
      <TextField
        label="Período (mês)"
        name="period"
        type="date"
        required
        hint="Qualquer dia do mês de referência."
        error={errors.period}
      />
      <TextField
        label="Variação (%)"
        name="rate_percent"
        required
        inputMode="decimal"
        pattern="-?[0-9]+([.][0-9]{1,6})?"
        hint="Ex.: 4.5 para 4,5%"
        error={errors.rate_percent}
      />
      <TextField
        label="Nota de origem"
        name="source_note"
        required
        maxLength={300}
        hint="De onde veio o valor (publicação, data de consulta)."
        error={errors.source_note}
      />
      <CheckboxField label="Valor demonstrativo (não oficial)" name="is_demonstrative" />
      <Button type="submit" busy={register.isPending}>
        Registrar valor
      </Button>
    </form>
  );
}

function Adjustments({ contractId }: { contractId: string }) {
  const key = useOrgKey();
  const { can } = useSession();
  const notify = useNotify();
  const invalidate = useInvalidate();
  const path = { params: { path: { contract_id: contractId } } };
  const adjustments = useQuery({
    queryKey: key("contracts", "finance", "adjustments", contractId),
    queryFn: () => unwrap(api.GET("/api/v1/contracts/{contract_id}/finance/adjustments", path)),
  });
  const indexes = useQuery({
    queryKey: key("indexes"),
    queryFn: () => unwrap(api.GET("/api/v1/indexes")),
  });
  const apply = useMutation({
    mutationFn: (body: Schemas["AdjustmentCreateRequest"]) =>
      unwrap(api.POST("/api/v1/contracts/{contract_id}/finance/adjustments", { ...path, body })),
    onSuccess: async () => {
      notify.success("Reajuste aplicado às parcelas previstas.");
      await invalidate("contracts", "finance");
    },
  });

  return (
    <Section title="Reajustes">
      <QueryView query={adjustments}>
        {(items) =>
          items.length === 0 ? (
            <EmptyState title="Nenhum reajuste aplicado." />
          ) : (
            <Table caption="Reajustes aplicados">
              <thead>
                <tr>
                  <th scope="col">Taxa</th>
                  <th scope="col">A partir de</th>
                </tr>
              </thead>
              <tbody>
                {items.map((adjustment) => (
                  <tr key={adjustment.id}>
                    <td>{adjustment.rate_percent.replace(".", ",")}%</td>
                    <td>{formatDate(adjustment.applies_from)}</td>
                  </tr>
                ))}
              </tbody>
            </Table>
          )
        }
      </QueryView>
      {can("finance.write") && <IndexValueForm />}
      {can("finance.write") && (
        <form
          className="form"
          noValidate
          onSubmit={(event) => {
            event.preventDefault();
            const values = formText(event.currentTarget);
            apply.mutate({
              index_value_id: values.index_value_id ?? "",
              applies_from: values.applies_from ?? "",
            });
          }}
        >
          <FormError message={apply.isError ? errorMessage(apply.error) : null} />
          <SelectField
            label="Índice"
            name="index_value_id"
            required
            placeholder="Selecione"
            options={(indexes.data ?? []).map((index) => ({
              value: index.id,
              label: `${index.series_name} ${formatDate(index.period)} — ${index.rate_percent}%${index.is_demonstrative ? " (demonstrativo)" : ""}`,
            }))}
            hint="Índices são cadastrados pela organização, com origem informada; nenhum índice oficial é embutido."
          />
          <TextField label="Aplicar a partir de" name="applies_from" type="date" required />
          <Button type="submit" busy={apply.isPending}>
            Aplicar reajuste
          </Button>
        </form>
      )}
    </Section>
  );
}

function Finance() {
  const contract = useContractContext();
  const key = useOrgKey();
  const { can } = useSession();
  const [confirming, setConfirming] = useState<Schemas["Installment"] | null>(null);
  const summary = useQuery({
    queryKey: key("contracts", "finance", contract.id),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/contracts/{contract_id}/finance", {
          params: { path: { contract_id: contract.id } },
        }),
      ),
  });

  return (
    <>
      <QueryView query={summary}>
        {(data) => (
          <>
            <div className="metrics">
              <div className="metric">
                <p className="metric__value">{formatMoney(data.contracted)}</p>
                <p className="metric__label">Contratado</p>
              </div>
              <div className="metric">
                <p className="metric__value">{formatMoney(data.projected)}</p>
                <p className="metric__label">Previsto em parcelas</p>
              </div>
              <div className="metric">
                <p className="metric__value">{formatMoney(data.confirmed)}</p>
                <p className="metric__label">Confirmado</p>
              </div>
              <div className="metric">
                <p className="metric__value">{formatMoney(data.overdue)}</p>
                <p className="metric__label">Em atraso</p>
              </div>
            </div>
            <Section title="Parcelas e marcos">
              {data.installments.length === 0 ? (
                <EmptyState title="Nenhuma parcela prevista." />
              ) : (
                <Table caption="Parcelas e marcos">
                  <thead>
                    <tr>
                      <th scope="col">Nº</th>
                      <th scope="col">Tipo</th>
                      <th scope="col">Descrição</th>
                      <th scope="col">Vencimento</th>
                      <th scope="col">Valor</th>
                      <th scope="col">Situação</th>
                      <th scope="col">
                        <span className="visually-hidden">Ações</span>
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.installments.map((installment) => (
                      <tr key={installment.id}>
                        <td>{installment.sequence}</td>
                        <td>{INSTALLMENT_KIND[installment.kind]}</td>
                        <td>{installment.description || "—"}</td>
                        <td>{formatDate(installment.due_date)}</td>
                        <td>{formatMoney(installment.amount)}</td>
                        <td>
                          <Badge tone={installment.status === "confirmed" ? "success" : "neutral"}>
                            {INSTALLMENT_STATUS[installment.status]}
                          </Badge>
                        </td>
                        <td>
                          {installment.status === "planned" && can("finance.write") && (
                            <Button
                              variant="link"
                              onClick={() => {
                                setConfirming(installment);
                              }}
                            >
                              Confirmar pagamento
                            </Button>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </Table>
              )}
            </Section>
          </>
        )}
      </QueryView>
      {can("finance.write") && <ScheduleForms contractId={contract.id} />}
      <Adjustments contractId={contract.id} />
      {confirming && (
        <ConfirmDialog
          installment={confirming}
          onClose={() => {
            setConfirming(null);
          }}
        />
      )}
    </>
  );
}

export function FinanceTab() {
  return (
    <Restricted permission="finance.read">
      <Finance />
    </Restricted>
  );
}
