import { useMutation } from "@tanstack/react-query";
import type { SubmitEvent } from "react";

import { api, unwrap, type Schemas } from "../../api/client";
import { ApiError, errorMessage } from "../../api/errors";
import { useDepartments, useDirectory, useInvalidate } from "../../app/queries";
import { FormError, SelectField, TextField, formText } from "../../components/forms";
import { useNotify } from "../../components/notifications";
import { WhenReady } from "../../components/states";
import { Button, DefinitionList, Section } from "../../components/ui";
import { decimalText, formatDate, formatDateTime, formatMoney } from "../../lib/format";
import { CONFIDENTIALITY, CONTRACT_TYPE, enumValue, options, PARTY_ROLE } from "../../lib/labels";
import { useContractContext } from "./ContractLayout";

export function OverviewTab() {
  const contract = useContractContext();
  const notify = useNotify();
  const invalidate = useInvalidate();
  const directory = useDirectory();
  const departments = useDepartments();
  const update = useMutation({
    mutationFn: (body: Schemas["PatchedContractUpdateRequest"]) =>
      unwrap(api.PATCH("/api/v1/contracts/{id}", { params: { path: { id: contract.id } }, body })),
    onSuccess: async () => {
      notify.success("Dados do contrato atualizados.");
      await invalidate("contracts");
    },
  });
  const errors = update.error instanceof ApiError ? update.error.fieldErrors : {};

  function handleSubmit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    const values = formText(event.currentTarget);
    update.mutate({
      expected_version: contract.version,
      title: values.title ?? contract.title,
      owner_id: values.owner_id ?? contract.owner_id,
      department_id: values.department_id ?? null,
      confidentiality:
        enumValue(CONFIDENTIALITY, values.confidentiality) ?? contract.confidentiality,
      total_value: values.total_value ? decimalText(values.total_value) : null,
      planned_start: values.planned_start ?? null,
      planned_end: values.planned_end ?? null,
    });
  }

  return (
    <>
      <DefinitionList
        items={[
          ["Tipo", CONTRACT_TYPE[contract.contract_type]],
          [
            "Contraparte",
            `${contract.counterparty_name} (${PARTY_ROLE[contract.counterparty_role]})`,
          ],
          ["Nossa organização", PARTY_ROLE[contract.organization_role]],
          ["Responsável", contract.owner_name],
          ["Departamento", contract.department_name || "—"],
          ["Confidencialidade", CONFIDENTIALITY[contract.confidentiality]],
          ["Valor total", formatMoney(contract.total_value)],
          [
            "Período previsto",
            `${formatDate(contract.planned_start)} a ${formatDate(contract.planned_end)}`,
          ],
          [
            "Revisão atual",
            contract.current_revision
              ? `nº ${contract.current_revision.number}`
              : "Nenhuma publicada",
          ],
          ["Etiquetas", contract.tags.length > 0 ? contract.tags.join(", ") : "—"],
          ["Atualizado em", formatDateTime(contract.updated_at)],
        ]}
      />
      {contract.can_edit && (
        <Section title="Editar dados">
          <p className="muted">
            Alterar dados depois da aprovação invalida a aprovação concedida: o contrato precisa ser
            aprovado de novo.
          </p>
          <WhenReady queries={[directory, departments]}>
            {() => (
              <form
                className="form form--wide"
                onSubmit={handleSubmit}
                noValidate
                key={contract.version}
              >
                <FormError message={update.isError ? errorMessage(update.error) : null} />
                <TextField
                  label="Título"
                  name="title"
                  required
                  defaultValue={contract.title}
                  error={errors.title}
                />
                <SelectField
                  label="Responsável"
                  name="owner_id"
                  defaultValue={contract.owner_id}
                  options={(directory.data ?? []).map((m) => ({
                    value: m.user_id,
                    label: m.full_name || m.email,
                  }))}
                  error={errors.owner_id}
                />
                <SelectField
                  label="Departamento"
                  name="department_id"
                  defaultValue={contract.department_id ?? ""}
                  placeholder="Sem departamento"
                  options={(departments.data ?? []).map((d) => ({ value: d.id, label: d.name }))}
                  error={errors.department_id}
                />
                <SelectField
                  label="Confidencialidade"
                  name="confidentiality"
                  defaultValue={contract.confidentiality}
                  options={options(CONFIDENTIALITY)}
                  error={errors.confidentiality}
                />
                <div className="columns">
                  <TextField
                    label="Valor total (R$)"
                    name="total_value"
                    inputMode="decimal"
                    defaultValue={contract.total_value ?? ""}
                    error={errors.total_value}
                  />
                  <TextField
                    label="Início previsto"
                    name="planned_start"
                    type="date"
                    defaultValue={contract.planned_start ?? ""}
                    error={errors.planned_start}
                  />
                  <TextField
                    label="Fim previsto"
                    name="planned_end"
                    type="date"
                    defaultValue={contract.planned_end ?? ""}
                    error={errors.planned_end}
                  />
                </div>
                <Button type="submit" busy={update.isPending}>
                  Salvar alterações
                </Button>
              </form>
            )}
          </WhenReady>
        </Section>
      )}
    </>
  );
}
