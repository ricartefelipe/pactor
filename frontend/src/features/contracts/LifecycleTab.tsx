import { useMutation, useQuery } from "@tanstack/react-query";
import { useState, type SubmitEvent } from "react";

import { api, unwrap, type Schemas } from "../../api/client";
import { ApiError, errorMessage } from "../../api/errors";
import { useInvalidate, useMemberName, useOrgKey } from "../../app/queries";
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
import { Button, DefinitionList, Section, Table } from "../../components/ui";
import { formatDate, formatDateTime, formatMoney, todayIso } from "../../lib/format";
import { AMENDMENT_FIELD, LIFECYCLE_STATUS, enumValue, labelOf, options } from "../../lib/labels";
import { useContractContext } from "./ContractLayout";
import { useLifecycle } from "./queries";

type Operation = "suspend" | "resume" | "terminate" | "archive";

const OPERATIONS: Readonly<
  Record<Operation, { label: string; allowedFrom: readonly Schemas["LifecycleStatusEnum"][] }>
> = {
  suspend: { label: "Suspender", allowedFrom: ["active"] },
  resume: { label: "Retomar", allowedFrom: ["suspended"] },
  terminate: { label: "Rescindir", allowedFrom: ["active", "suspended", "awaiting_start"] },
  archive: { label: "Arquivar", allowedFrom: ["ended", "terminated"] },
};

function Conditions({ contractId }: { contractId: string }) {
  const key = useOrgKey();
  const [on, setOn] = useState(todayIso());
  const conditions = useQuery({
    queryKey: key("contracts", "conditions", contractId, on),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/contracts/{contract_id}/conditions", {
          params: { path: { contract_id: contractId }, query: { on } },
        }),
      ),
  });
  return (
    <Section title="Condições vigentes em uma data">
      <div className="filters">
        <TextField
          label="Data"
          name="on"
          type="date"
          value={on}
          onChange={(event) => {
            setOn(event.target.value);
          }}
        />
      </div>
      <QueryView query={conditions}>
        {(data) => (
          <DefinitionList
            items={[
              ["Valor total", formatMoney(data.total_value)],
              ["Fim da vigência", formatDate(data.effective_end)],
              ["Descrição", data.description || "—"],
            ]}
          />
        )}
      </QueryView>
    </Section>
  );
}

function AmendmentChanges({ contractId }: { contractId: string }) {
  const key = useOrgKey();
  const notify = useNotify();
  const invalidate = useInvalidate();
  const path = { params: { path: { contract_id: contractId } } };
  const changes = useQuery({
    queryKey: key("contracts", "amendment-changes", contractId),
    queryFn: () => unwrap(api.GET("/api/v1/contracts/{contract_id}/amendment-changes", path)),
  });
  const add = useMutation({
    mutationFn: (body: Schemas["AmendmentChangeRequest"]) =>
      unwrap(api.POST("/api/v1/contracts/{contract_id}/amendment-changes", { ...path, body })),
    onSuccess: async () => {
      notify.success("Alteração registrada no aditivo.");
      await invalidate("contracts");
    },
  });
  const errors = add.error instanceof ApiError ? add.error.fieldErrors : {};

  function handleSubmit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    const values = formText(event.currentTarget);
    const field = enumValue(AMENDMENT_FIELD, values.field);
    if (field === undefined) return;
    add.mutate({ field, value: values.value ?? "", effective_from: values.effective_from ?? "" });
  }

  return (
    <Section title="Alterações deste aditivo">
      <p className="muted">
        Valem para o contrato original a partir da data indicada e somente depois que o aditivo for
        assinado. O texto original não é reescrito.
      </p>
      <QueryView query={changes}>
        {(items) =>
          items.length === 0 ? (
            <EmptyState title="Nenhuma alteração registrada." />
          ) : (
            <Table caption="Alterações do aditivo">
              <thead>
                <tr>
                  <th scope="col">Campo</th>
                  <th scope="col">Novo valor</th>
                  <th scope="col">A partir de</th>
                </tr>
              </thead>
              <tbody>
                {items.map((change) => (
                  <tr key={change.id}>
                    <td>{AMENDMENT_FIELD[change.field]}</td>
                    <td>{change.value}</td>
                    <td>{formatDate(change.effective_from)}</td>
                  </tr>
                ))}
              </tbody>
            </Table>
          )
        }
      </QueryView>
      <form className="form" onSubmit={handleSubmit} noValidate>
        <FormError message={add.isError ? errorMessage(add.error) : null} />
        <SelectField
          label="Campo"
          name="field"
          required
          options={options(AMENDMENT_FIELD)}
          error={errors.field}
        />
        <TextField
          label="Novo valor"
          name="value"
          required
          hint="Valor em reais com ponto (ex.: 42000.00) ou data AAAA-MM-DD."
          error={errors.value}
        />
        <TextField
          label="A partir de"
          name="effective_from"
          type="date"
          required
          error={errors.effective_from}
        />
        <Button type="submit" busy={add.isPending}>
          Registrar alteração
        </Button>
      </form>
    </Section>
  );
}

export function LifecycleTab() {
  const contract = useContractContext();
  const lifecycle = useLifecycle(contract.id);
  const { can } = useSession();
  const memberName = useMemberName();
  const notify = useNotify();
  const invalidate = useInvalidate();
  const [operation, setOperation] = useState<Operation | null>(null);
  const act = useMutation({
    mutationFn: ({ op, reason, date }: { op: Operation; reason: string; date: string }) =>
      unwrap(
        api.POST("/api/v1/contracts/{contract_id}/lifecycle/{operation}", {
          params: { path: { contract_id: contract.id, operation: op } },
          body: { reason, effective_date: date },
        }),
      ),
    onSuccess: async () => {
      setOperation(null);
      notify.success("Vigência atualizada.");
      await invalidate("contracts");
    },
  });

  return (
    <QueryView query={lifecycle}>
      {(data) => (
        <>
          <DefinitionList
            items={[
              ["Situação", LIFECYCLE_STATUS[data.lifecycle_status]],
              ["Assinado em", formatDate(data.signed_on)],
              ["Início da vigência", formatDate(data.effective_start)],
              [
                "Fim da vigência",
                data.effective_end ? formatDate(data.effective_end) : "Prazo indeterminado",
              ],
            ]}
          />
          {data.lifecycle_status === "not_started" && (
            <p className="muted">
              A vigência começa depois da assinatura, na data de início prevista.
            </p>
          )}
          {data.lifecycle_status === "awaiting_start" && (
            <p className="muted">Assinado; o agendador ativa a vigência na data de início.</p>
          )}
          {can("contracts.lifecycle") && (
            <div className="actions">
              {(Object.keys(OPERATIONS) as Operation[])
                .filter((op) => OPERATIONS[op].allowedFrom.includes(data.lifecycle_status))
                .map((op) => (
                  <Button
                    key={op}
                    variant={op === "terminate" ? "danger" : "secondary"}
                    onClick={() => {
                      setOperation(op);
                    }}
                  >
                    {OPERATIONS[op].label}
                  </Button>
                ))}
            </div>
          )}
          <Dialog
            open={operation !== null}
            title={operation ? `${OPERATIONS[operation].label} contrato` : ""}
            submitLabel={operation ? OPERATIONS[operation].label : ""}
            submitVariant={operation === "terminate" ? "danger" : "primary"}
            busy={act.isPending}
            onClose={() => {
              setOperation(null);
            }}
            onSubmit={(form) => {
              if (operation) {
                act.mutate({
                  op: operation,
                  reason: textOf(form, "reason"),
                  date: textOf(form, "effective_date"),
                });
              }
            }}
          >
            <FormError message={act.isError ? errorMessage(act.error) : null} />
            <TextAreaField label="Motivo" name="reason" required rows={3} />
            <TextField
              label="Data de efeito"
              name="effective_date"
              type="date"
              required
              defaultValue={todayIso()}
            />
          </Dialog>
          <Section title="Histórico da vigência">
            {data.events.length === 0 ? (
              <EmptyState title="Nenhum evento de vigência." />
            ) : (
              <ol className="timeline">
                {data.events.map((event) => (
                  <li key={event.id}>
                    <strong>
                      {labelOf(LIFECYCLE_STATUS, event.from_status)} →{" "}
                      {labelOf(LIFECYCLE_STATUS, event.to_status)}
                    </strong>{" "}
                    em {formatDate(event.effective_date)} ·{" "}
                    {event.actor_id ? memberName(event.actor_id) : "agendador"} ·{" "}
                    {formatDateTime(event.created_at)}
                    {event.reason && <p className="muted">{event.reason}</p>}
                  </li>
                ))}
              </ol>
            )}
          </Section>
          <Conditions contractId={contract.id} />
          {contract.contract_type === "amendment" && <AmendmentChanges contractId={contract.id} />}
        </>
      )}
    </QueryView>
  );
}
