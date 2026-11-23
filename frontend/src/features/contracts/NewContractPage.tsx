import { useMutation } from "@tanstack/react-query";
import type { SubmitEvent } from "react";
import { useNavigate } from "react-router";

import { api, unwrap, type Schemas } from "../../api/client";
import { ApiError, errorMessage } from "../../api/errors";
import {
  useActiveCounterparties,
  useDepartments,
  useDirectory,
  useInvalidate,
} from "../../app/queries";
import { useSession } from "../../auth/session";
import { FormError, SelectField, TextAreaField, TextField, formText } from "../../components/forms";
import { useNotify } from "../../components/notifications";
import { Restricted } from "../../components/Restricted";
import { WhenReady } from "../../components/states";
import { Button, PageHeader } from "../../components/ui";
import { useIdempotencyKey } from "../../lib/idempotency";
import { decimalText } from "../../lib/format";
import { CONFIDENTIALITY, CONTRACT_TYPE, enumValue, options, PARTY_ROLE } from "../../lib/labels";

function NewContractForm() {
  const { me } = useSession();
  const navigate = useNavigate();
  const notify = useNotify();
  const invalidate = useInvalidate();
  const counterparties = useActiveCounterparties();
  const departments = useDepartments();
  const directory = useDirectory();
  const idempotency = useIdempotencyKey();
  const create = useMutation({
    mutationFn: (body: Schemas["ContractCreateRequest"]) =>
      unwrap(
        api.POST("/api/v1/contracts", {
          params: { header: { "Idempotency-Key": idempotency.key } },
          body,
        }),
      ),
    onSuccess: async (contract) => {
      idempotency.settle();
      notify.success(`Contrato ${contract.reference} criado.`);
      await invalidate("contracts");
      await navigate(`/contratos/${contract.id}/rascunho`);
    },
    onError: (error) => {
      idempotency.settle(error);
    },
  });
  const errors = create.error instanceof ApiError ? create.error.fieldErrors : {};

  function handleSubmit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    const values = formText(event.currentTarget);
    const contractType = enumValue(CONTRACT_TYPE, values.contract_type);
    const organizationRole = enumValue(PARTY_ROLE, values.organization_role);
    const counterpartyRole = enumValue(PARTY_ROLE, values.counterparty_role);
    const confidentiality = enumValue(CONFIDENTIALITY, values.confidentiality) ?? "internal";
    if (!contractType || !organizationRole || !counterpartyRole) return;
    const tags = (values.tags ?? "")
      .split(",")
      .map((tag) => tag.trim())
      .filter(Boolean);
    create.mutate({
      title: values.title ?? "",
      contract_type: contractType,
      counterparty_id: values.counterparty_id ?? "",
      organization_role: organizationRole,
      counterparty_role: counterpartyRole,
      owner_id: values.owner_id ?? me.id,
      confidentiality,
      classification: values.classification ?? "",
      tags,
      department_id: values.department_id ?? null,
      total_value: values.total_value ? decimalText(values.total_value) : null,
      planned_start: values.planned_start ?? null,
      planned_end: values.planned_end ?? null,
      ...(values.initial_content ? { initial_content: values.initial_content } : {}),
    });
  }

  return (
    <>
      <PageHeader
        title="Novo contrato"
        description="O contrato nasce em elaboração, com um rascunho editável."
      />
      <WhenReady queries={[counterparties, departments, directory]}>
        {() => (
          <form className="form form--wide" onSubmit={handleSubmit} noValidate>
            <FormError message={create.isError ? errorMessage(create.error) : null} />
            <TextField label="Título" name="title" required maxLength={200} error={errors.title} />
            <SelectField
              label="Tipo"
              name="contract_type"
              required
              options={options(CONTRACT_TYPE)}
              error={errors.contract_type}
            />
            <SelectField
              label="Contraparte"
              name="counterparty_id"
              required
              placeholder="Selecione"
              options={(counterparties.data?.results ?? []).map((c) => ({
                value: c.id,
                label: c.name,
              }))}
              error={errors.counterparty_id}
            />
            <div className="columns">
              <SelectField
                label="Papel da nossa organização"
                name="organization_role"
                required
                defaultValue="contractor"
                options={options(PARTY_ROLE)}
                error={errors.organization_role}
              />
              <SelectField
                label="Papel da contraparte"
                name="counterparty_role"
                required
                defaultValue="contracted"
                options={options(PARTY_ROLE)}
                error={errors.counterparty_role}
              />
            </div>
            <SelectField
              label="Responsável"
              name="owner_id"
              required
              defaultValue={me.id}
              options={(directory.data ?? []).map((m) => ({
                value: m.user_id,
                label: m.full_name || m.email,
              }))}
              error={errors.owner_id}
            />
            <SelectField
              label="Departamento"
              name="department_id"
              placeholder="Sem departamento"
              options={(departments.data ?? []).map((d) => ({ value: d.id, label: d.name }))}
              error={errors.department_id}
            />
            <SelectField
              label="Confidencialidade"
              name="confidentiality"
              required
              defaultValue="internal"
              options={options(CONFIDENTIALITY)}
              hint="Restrito: apenas o departamento. Confidencial: responsável e pessoas com acesso concedido."
              error={errors.confidentiality}
            />
            <div className="columns">
              <TextField
                label="Valor total (R$)"
                name="total_value"
                inputMode="decimal"
                pattern="[0-9]+([.,][0-9]{1,2})?"
                hint="Ex.: 36000,00"
                error={errors.total_value}
              />
              <TextField
                label="Início previsto"
                name="planned_start"
                type="date"
                error={errors.planned_start}
              />
              <TextField
                label="Fim previsto"
                name="planned_end"
                type="date"
                error={errors.planned_end}
              />
            </div>
            <TextField
              label="Classificação"
              name="classification"
              maxLength={60}
              error={errors.classification}
            />
            <TextField
              label="Etiquetas"
              name="tags"
              hint="Separadas por vírgula."
              error={errors.tags}
            />
            <TextAreaField
              label="Texto inicial do rascunho"
              name="initial_content"
              rows={8}
              error={errors.initial_content}
            />
            <Button type="submit" busy={create.isPending}>
              Criar contrato
            </Button>
          </form>
        )}
      </WhenReady>
    </>
  );
}

export function NewContractPage() {
  return (
    <Restricted permission="contracts.create">
      <NewContractForm />
    </Restricted>
  );
}
