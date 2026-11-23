import { useMutation, useQuery } from "@tanstack/react-query";
import type { SubmitEvent } from "react";
import { useNavigate, useParams } from "react-router";

import { api, unwrap, type Schemas } from "../../api/client";
import { ApiError, errorMessage } from "../../api/errors";
import { useInvalidate, useOrgKey } from "../../app/queries";
import { useSession } from "../../auth/session";
import { FormError, SelectField, TextAreaField, TextField, formText } from "../../components/forms";
import { useNotify } from "../../components/notifications";
import { Restricted } from "../../components/Restricted";
import { QueryView } from "../../components/states";
import { Badge, Button, DefinitionList, PageHeader, Section } from "../../components/ui";
import { formatDateTime } from "../../lib/format";
import { enumValue, options, PARTY_KIND } from "../../lib/labels";

function NewCounterpartyForm() {
  const navigate = useNavigate();
  const notify = useNotify();
  const invalidate = useInvalidate();
  const create = useMutation({
    mutationFn: (body: Schemas["CounterpartyRequest"]) =>
      unwrap(api.POST("/api/v1/counterparties", { body })),
    onSuccess: async (created) => {
      notify.success("Contraparte cadastrada.");
      await invalidate("counterparties");
      await navigate(`/contrapartes/${created.id}`);
    },
  });
  const errors = create.error instanceof ApiError ? create.error.fieldErrors : {};

  function handleSubmit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    const values = formText(event.currentTarget);
    const kind = enumValue(PARTY_KIND, values.kind);
    if (kind === undefined) return;
    create.mutate({
      kind,
      name: values.name ?? "",
      document: values.document ?? "",
      ...(values.trade_name ? { trade_name: values.trade_name } : {}),
      ...(values.email ? { email: values.email } : {}),
      ...(values.phone ? { phone: values.phone } : {}),
      ...(values.notes ? { notes: values.notes } : {}),
    });
  }

  return (
    <>
      <PageHeader title="Nova contraparte" />
      <form className="form" onSubmit={handleSubmit} noValidate>
        <FormError message={create.isError ? errorMessage(create.error) : null} />
        <SelectField
          label="Tipo"
          name="kind"
          required
          options={options(PARTY_KIND)}
          error={errors.kind}
        />
        <TextField
          label="Nome ou razão social"
          name="name"
          required
          maxLength={200}
          error={errors.name}
        />
        <TextField
          label="Nome fantasia"
          name="trade_name"
          maxLength={200}
          error={errors.trade_name}
        />
        <TextField
          label="CPF ou CNPJ"
          name="document"
          required
          inputMode="numeric"
          hint="Somente números ou com pontuação; os dígitos verificadores são conferidos."
          error={errors.document}
        />
        <TextField label="E-mail" name="email" type="email" error={errors.email} />
        <TextField label="Telefone" name="phone" type="tel" error={errors.phone} />
        <TextAreaField label="Observações" name="notes" rows={3} error={errors.notes} />
        <Button type="submit" busy={create.isPending}>
          Cadastrar
        </Button>
      </form>
    </>
  );
}

export function NewCounterpartyPage() {
  return (
    <Restricted permission="counterparties.write">
      <NewCounterpartyForm />
    </Restricted>
  );
}

function CounterpartyDetail({ id }: { id: string }) {
  const key = useOrgKey();
  const { can } = useSession();
  const notify = useNotify();
  const invalidate = useInvalidate();
  const detail = useQuery({
    queryKey: key("counterparties", "detail", id),
    queryFn: () => unwrap(api.GET("/api/v1/counterparties/{id}", { params: { path: { id } } })),
  });
  const update = useMutation({
    mutationFn: (body: Schemas["PatchedCounterpartyUpdateRequest"]) =>
      unwrap(api.PATCH("/api/v1/counterparties/{id}", { params: { path: { id } }, body })),
    onSuccess: async () => {
      notify.success("Contraparte atualizada.");
      await invalidate("counterparties");
    },
  });
  const errors = update.error instanceof ApiError ? update.error.fieldErrors : {};

  return (
    <QueryView query={detail}>
      {(counterparty) => {
        function handleSubmit(event: SubmitEvent<HTMLFormElement>) {
          event.preventDefault();
          const form = event.currentTarget;
          const data = new FormData(form);
          const text = (name: string) => {
            const value = data.get(name);
            return typeof value === "string" ? value.trim() : "";
          };
          update.mutate({
            expected_version: counterparty.version,
            name: text("name"),
            trade_name: text("trade_name"),
            email: text("email"),
            phone: text("phone"),
            notes: text("notes"),
            is_active: data.get("is_active") === "on",
          });
        }
        return (
          <>
            <PageHeader
              title={counterparty.name}
              description={
                <Badge tone={counterparty.is_active ? "success" : "neutral"}>
                  {counterparty.is_active ? "Ativa" : "Inativa"}
                </Badge>
              }
            />
            <DefinitionList
              items={[
                ["Tipo", PARTY_KIND[counterparty.kind]],
                ["Documento", counterparty.document],
                ["Atualizada em", formatDateTime(counterparty.updated_at)],
              ]}
            />
            {can("counterparties.write") && (
              <Section title="Editar dados">
                <form
                  className="form"
                  onSubmit={handleSubmit}
                  noValidate
                  key={counterparty.version}
                >
                  <FormError message={update.isError ? errorMessage(update.error) : null} />
                  <TextField
                    label="Nome ou razão social"
                    name="name"
                    required
                    defaultValue={counterparty.name}
                    error={errors.name}
                  />
                  <TextField
                    label="Nome fantasia"
                    name="trade_name"
                    defaultValue={counterparty.trade_name ?? ""}
                    error={errors.trade_name}
                  />
                  <TextField
                    label="E-mail"
                    name="email"
                    type="email"
                    defaultValue={counterparty.email ?? ""}
                    error={errors.email}
                  />
                  <TextField
                    label="Telefone"
                    name="phone"
                    type="tel"
                    defaultValue={counterparty.phone ?? ""}
                    error={errors.phone}
                  />
                  <TextAreaField
                    label="Observações"
                    name="notes"
                    rows={3}
                    defaultValue={counterparty.notes ?? ""}
                    error={errors.notes}
                  />
                  <div className="field field--checkbox">
                    <input
                      id="cp-ativa"
                      type="checkbox"
                      name="is_active"
                      defaultChecked={counterparty.is_active}
                    />
                    <label htmlFor="cp-ativa">
                      Contraparte ativa (pode ser usada em novos contratos)
                    </label>
                  </div>
                  <Button type="submit" busy={update.isPending}>
                    Salvar alterações
                  </Button>
                </form>
              </Section>
            )}
          </>
        );
      }}
    </QueryView>
  );
}

export function CounterpartyPage() {
  const { id = "" } = useParams();
  return (
    <Restricted permission="counterparties.read">
      <CounterpartyDetail id={id} />
    </Restricted>
  );
}
