import { useMutation, useQuery } from "@tanstack/react-query";
import { useCallback, useState, type SubmitEvent } from "react";

import { api, unwrap, type Schemas } from "../../api/client";
import { ApiError, errorMessage } from "../../api/errors";
import { useDirectory, useInvalidate, useMemberName, useOrgKey } from "../../app/queries";
import { useSession } from "../../auth/session";
import { Dialog } from "../../components/dialog";
import { FileUpload } from "../../components/FileUpload";
import {
  CheckboxField,
  FormError,
  SelectField,
  TextAreaField,
  TextField,
  formText,
  textOf,
} from "../../components/forms";
import { useNotify } from "../../components/notifications";
import { EmptyState, QueryView, WhenReady } from "../../components/states";
import { Badge, Button, Section, Table } from "../../components/ui";
import { formatDate, todayIso } from "../../lib/format";
import {
  FREQUENCY,
  OBLIGATION_CATEGORY,
  OCCURRENCE_STATUS,
  RESPONSIBLE_PARTY,
  enumValue,
  options,
} from "../../lib/labels";
import { useContractContext } from "./ContractLayout";

type Occurrence = Schemas["Occurrence"];
type Obligation = Schemas["Obligation"];

function CompleteDialog({
  occurrence,
  obligation,
  onClose,
}: {
  occurrence: Occurrence;
  obligation: Obligation;
  onClose: () => void;
}) {
  const notify = useNotify();
  const invalidate = useInvalidate();
  const [evidence, setEvidence] = useState<string | null>(null);
  const complete = useMutation({
    mutationFn: (note: string) =>
      unwrap(
        api.POST("/api/v1/occurrences/{occurrence_id}/complete", {
          params: { path: { occurrence_id: occurrence.id } },
          body: { note, ...(evidence ? { evidence_id: evidence } : {}) },
        }),
      ),
    onSuccess: async (updated) => {
      notify.success(
        updated.status === "submitted"
          ? "Conclusão enviada para aprovação."
          : "Ocorrência concluída.",
      );
      onClose();
      await invalidate("contracts", "obligations");
    },
  });
  const onReleased = useCallback((file: Schemas["StoredFile"]) => {
    setEvidence(file.id);
  }, []);
  return (
    <Dialog
      open
      title={`Concluir: ${obligation.title} (${formatDate(occurrence.due_date)})`}
      submitLabel="Concluir"
      busy={complete.isPending}
      onClose={onClose}
      onSubmit={(form) => {
        complete.mutate(textOf(form, "note"));
      }}
    >
      <FormError message={complete.isError ? errorMessage(complete.error) : null} />
      {obligation.requires_evidence && (
        <FileUpload
          label="Evidência (obrigatória)"
          purpose="obligation_evidence"
          onReleased={onReleased}
        />
      )}
      <TextAreaField label="Observação" name="note" rows={3} />
    </Dialog>
  );
}

function ObligationCard({ obligation }: { obligation: Obligation }) {
  const { can, me } = useSession();
  const memberName = useMemberName();
  const notify = useNotify();
  const invalidate = useInvalidate();
  const [completing, setCompleting] = useState<Occurrence | null>(null);
  const review = useMutation({
    mutationFn: ({ id, approve }: { id: string; approve: boolean }) =>
      unwrap(
        api.POST("/api/v1/occurrences/{occurrence_id}/review", {
          params: { path: { occurrence_id: id } },
          body: { approve },
        }),
      ),
    onSuccess: async (_, { approve }) => {
      notify.success(approve ? "Conclusão aprovada." : "Conclusão devolvida ao responsável.");
      await invalidate("contracts", "obligations");
    },
  });
  const canComplete =
    can("obligations.complete") &&
    (obligation.responsible_id === me.id || can("obligations.manage"));

  return (
    <article className="card">
      <h3>{obligation.title}</h3>
      <p className="muted">
        {OBLIGATION_CATEGORY[obligation.category]} · {FREQUENCY[obligation.frequency]} · responsável{" "}
        {memberName(obligation.responsible_id)} ({RESPONSIBLE_PARTY[obligation.responsible_party]})
        {obligation.requires_evidence && " · exige evidência"}
        {obligation.requires_approval && " · conclusão aprovada pela gestão"}
      </p>
      {obligation.description && <p>{obligation.description}</p>}
      <Table caption={`Ocorrências de ${obligation.title}`}>
        <thead>
          <tr>
            <th scope="col">Nº</th>
            <th scope="col">Vencimento</th>
            <th scope="col">Situação</th>
            <th scope="col">
              <span className="visually-hidden">Ações</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {obligation.occurrences.map((occurrence) => {
            const overdue = occurrence.status === "pending" && occurrence.due_date < todayIso();
            return (
              <tr key={occurrence.id}>
                <td>{occurrence.sequence}</td>
                <td>{formatDate(occurrence.due_date)}</td>
                <td>
                  <Badge
                    tone={
                      occurrence.status === "completed" ? "success" : overdue ? "danger" : "neutral"
                    }
                  >
                    {overdue ? "Em atraso" : OCCURRENCE_STATUS[occurrence.status]}
                  </Badge>
                </td>
                <td>
                  {occurrence.status === "pending" && canComplete && (
                    <Button
                      variant="link"
                      onClick={() => {
                        setCompleting(occurrence);
                      }}
                    >
                      Concluir
                    </Button>
                  )}
                  {occurrence.status === "submitted" && can("obligations.manage") && (
                    <>
                      <Button
                        variant="link"
                        onClick={() => {
                          review.mutate({ id: occurrence.id, approve: true });
                        }}
                      >
                        Aprovar
                      </Button>
                      <Button
                        variant="link"
                        onClick={() => {
                          review.mutate({ id: occurrence.id, approve: false });
                        }}
                      >
                        Devolver
                      </Button>
                    </>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </Table>
      <FormError message={review.isError ? errorMessage(review.error) : null} />
      {completing && (
        <CompleteDialog
          occurrence={completing}
          obligation={obligation}
          onClose={() => {
            setCompleting(null);
          }}
        />
      )}
    </article>
  );
}

function ObligationForm({
  contractId,
  existing,
}: {
  contractId: string;
  existing: readonly Obligation[];
}) {
  const { me } = useSession();
  const directory = useDirectory();
  const notify = useNotify();
  const invalidate = useInvalidate();
  const create = useMutation({
    mutationFn: (body: Schemas["ObligationCreateRequest"]) =>
      unwrap(
        api.POST("/api/v1/contracts/{contract_id}/obligations", {
          params: { path: { contract_id: contractId } },
          body,
        }),
      ),
    onSuccess: async () => {
      notify.success("Obrigação criada com suas ocorrências.");
      await invalidate("contracts", "obligations");
    },
  });
  const errors = create.error instanceof ApiError ? create.error.fieldErrors : {};

  function handleSubmit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const values = formText(form);
    const category = enumValue(OBLIGATION_CATEGORY, values.category);
    const party = enumValue(RESPONSIBLE_PARTY, values.responsible_party);
    const frequency = enumValue(FREQUENCY, values.frequency);
    if (!category || !party || !frequency) return;
    const data = new FormData(form);
    create.mutate({
      title: values.title ?? "",
      description: values.description ?? "",
      category,
      responsible_id: values.responsible_id ?? me.id,
      responsible_party: party,
      frequency,
      interval: Number(values.interval ?? "1"),
      first_due: values.first_due ?? "",
      until: values.until ?? null,
      count: values.count ? Number(values.count) : null,
      requires_evidence: data.get("requires_evidence") === "on",
      requires_approval: data.get("requires_approval") === "on",
      reminder_days_before: Number(values.reminder_days_before ?? "0"),
      escalation_days_after: Number(values.escalation_days_after ?? "0"),
      depends_on_id: values.depends_on_id ?? null,
    });
  }

  return (
    <WhenReady queries={[directory]}>
      {() => (
        <form className="form form--wide" onSubmit={handleSubmit} noValidate>
          <FormError message={create.isError ? errorMessage(create.error) : null} />
          <TextField label="Título" name="title" required maxLength={200} error={errors.title} />
          <TextAreaField label="Descrição" name="description" rows={2} error={errors.description} />
          <div className="columns">
            <SelectField
              label="Categoria"
              name="category"
              required
              options={options(OBLIGATION_CATEGORY)}
              error={errors.category}
            />
            <SelectField
              label="Parte responsável"
              name="responsible_party"
              required
              defaultValue="organization"
              options={options(RESPONSIBLE_PARTY)}
              error={errors.responsible_party}
            />
            <SelectField
              label="Responsável interno"
              name="responsible_id"
              required
              defaultValue={me.id}
              options={(directory.data ?? []).map((m) => ({
                value: m.user_id,
                label: m.full_name || m.email,
              }))}
              error={errors.responsible_id}
            />
          </div>
          <div className="columns">
            <SelectField
              label="Frequência"
              name="frequency"
              required
              defaultValue="monthly"
              options={options(FREQUENCY)}
              error={errors.frequency}
            />
            <TextField
              label="A cada (intervalo)"
              name="interval"
              type="number"
              min={1}
              defaultValue={1}
              error={errors.interval}
            />
            <TextField
              label="Primeiro vencimento"
              name="first_due"
              type="date"
              required
              error={errors.first_due}
            />
          </div>
          <div className="columns">
            <TextField label="Até (opcional)" name="until" type="date" error={errors.until} />
            <TextField
              label="Número de ocorrências (opcional)"
              name="count"
              type="number"
              min={1}
              error={errors.count}
            />
          </div>
          <div className="columns">
            <TextField
              label="Lembrete (dias antes)"
              name="reminder_days_before"
              type="number"
              min={0}
              defaultValue={5}
              error={errors.reminder_days_before}
            />
            <TextField
              label="Escalonamento (dias depois)"
              name="escalation_days_after"
              type="number"
              min={0}
              defaultValue={2}
              error={errors.escalation_days_after}
            />
          </div>
          <SelectField
            label="Depende de"
            name="depends_on_id"
            placeholder="Nenhuma"
            options={existing.map((o) => ({ value: o.id, label: o.title }))}
            error={errors.depends_on_id}
          />
          <CheckboxField label="Exige evidência para concluir" name="requires_evidence" />
          <CheckboxField
            label="Conclusão precisa de aprovação da gestão"
            name="requires_approval"
          />
          <Button type="submit" busy={create.isPending}>
            Criar obrigação
          </Button>
        </form>
      )}
    </WhenReady>
  );
}

export function ObligationsTab() {
  const contract = useContractContext();
  const key = useOrgKey();
  const { can } = useSession();
  const obligations = useQuery({
    queryKey: key("contracts", "obligations", contract.id),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/contracts/{contract_id}/obligations", {
          params: { path: { contract_id: contract.id } },
        }),
      ),
  });
  return (
    <QueryView query={obligations}>
      {(items) => (
        <>
          <Section title="Obrigações">
            {items.length === 0 ? (
              <EmptyState title="Nenhuma obrigação cadastrada." />
            ) : (
              items.map((obligation) => (
                <ObligationCard key={obligation.id} obligation={obligation} />
              ))
            )}
          </Section>
          {can("obligations.manage") && (
            <Section title="Nova obrigação">
              <ObligationForm contractId={contract.id} existing={items} />
            </Section>
          )}
        </>
      )}
    </QueryView>
  );
}
