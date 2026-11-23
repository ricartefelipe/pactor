import { useMutation, useQuery } from "@tanstack/react-query";
import { useState, type SubmitEvent } from "react";
import { Link } from "react-router";

import { api, unwrap } from "../../api/client";
import { ApiError, errorMessage } from "../../api/errors";
import { useInvalidate, useOrgKey } from "../../app/queries";
import { useSession } from "../../auth/session";
import {
  CheckboxField,
  FormError,
  SelectField,
  TextAreaField,
  TextField,
  formText,
} from "../../components/forms";
import { useNotify } from "../../components/notifications";
import { QueryView } from "../../components/states";
import { Button, Section } from "../../components/ui";
import { formatDateTime } from "../../lib/format";
import { useIdempotencyKey } from "../../lib/idempotency";
import { generationValues, templateFields, type TemplateField } from "../../lib/templateDefinition";
import { useTemplates } from "../templates/TemplatesPages";
import { useContractContext } from "./ContractLayout";

function useDraft(contractId: string) {
  const key = useOrgKey();
  return useQuery({
    queryKey: key("contracts", "draft", contractId),
    queryFn: () =>
      unwrap(api.GET("/api/v1/contracts/{id}/draft", { params: { path: { id: contractId } } })),
  });
}

function GenerationField({ field }: { field: TemplateField }) {
  const common = { label: field.label, name: field.key, required: field.required };
  switch (field.type) {
    case "boolean":
      return <CheckboxField label={field.label} name={field.key} />;
    case "date":
      return <TextField {...common} type="date" />;
    case "decimal":
      return (
        <TextField
          {...common}
          inputMode="decimal"
          pattern="-?[0-9]+([.][0-9]+)?"
          hint="Use ponto como separador decimal."
        />
      );
    case "choice":
      return (
        <SelectField
          {...common}
          placeholder="Selecione"
          options={field.choices.map((c) => ({ value: c, label: c }))}
        />
      );
    case "text":
      return <TextField {...common} />;
  }
}

function GenerateFromTemplate({
  contractId,
  draftVersion,
}: {
  contractId: string;
  draftVersion: number;
}) {
  const templates = useTemplates();
  const notify = useNotify();
  const invalidate = useInvalidate();
  const idempotency = useIdempotencyKey();
  const [versionId, setVersionId] = useState("");
  const versions = (templates.data ?? []).flatMap((template) =>
    template.versions.map((version) => ({ template, version })),
  );
  const chosen = versions.find((item) => item.version.id === versionId);
  const fields = chosen ? templateFields(chosen.version.definition) : [];
  const generate = useMutation({
    mutationFn: (values: Record<string, string | boolean>) =>
      unwrap(
        api.POST("/api/v1/templates/generate", {
          params: { header: { "Idempotency-Key": idempotency.key } },
          body: {
            contract_id: contractId,
            template_version_id: versionId,
            values,
            expected_draft_version: draftVersion,
          },
        }),
      ),
    onSuccess: async (revision) => {
      idempotency.settle();
      notify.success(`Revisão ${revision.number} gerada a partir do modelo.`);
      await invalidate("contracts");
    },
    onError: (error) => {
      idempotency.settle(error);
    },
  });
  const errors = generate.error instanceof ApiError ? generate.error.fieldErrors : {};

  if (versions.length === 0) {
    return (
      <p>
        Nenhum modelo publicado. <Link to="/modelos">Ver modelos</Link>
      </p>
    );
  }
  return (
    <form
      className="form"
      noValidate
      onSubmit={(event) => {
        event.preventDefault();
        generate.mutate(generationValues(fields, new FormData(event.currentTarget)));
      }}
    >
      <FormError message={generate.isError ? errorMessage(generate.error) : null} />
      <SelectField
        label="Modelo e versão"
        name="template_version_id"
        required
        placeholder="Selecione"
        value={versionId}
        onChange={(event) => {
          setVersionId(event.target.value);
        }}
        options={versions.map(({ template, version }) => ({
          value: version.id,
          label: `${template.name} — versão ${version.number}`,
        }))}
        error={errors.template_version_id}
      />
      {fields.map((field) => (
        <GenerationField key={`${versionId}-${field.key}`} field={field} />
      ))}
      {chosen && (
        <Button type="submit" busy={generate.isPending}>
          Gerar e publicar revisão
        </Button>
      )}
    </form>
  );
}

export function DraftTab() {
  const contract = useContractContext();
  const { can } = useSession();
  const draft = useDraft(contract.id);
  const notify = useNotify();
  const invalidate = useInvalidate();
  const publishKey = useIdempotencyKey();

  const save = useMutation({
    mutationFn: ({ content, version }: { content: string; version: number }) =>
      unwrap(
        api.PUT("/api/v1/contracts/{id}/draft", {
          params: { path: { id: contract.id } },
          body: { content, expected_version: version },
        }),
      ),
    onSuccess: async () => {
      notify.success("Rascunho salvo.");
      await invalidate("contracts", "draft", contract.id);
    },
  });
  const publish = useMutation({
    mutationFn: ({ summary, version }: { summary: string; version: number }) =>
      unwrap(
        api.POST("/api/v1/contracts/{id}/revisions", {
          params: { path: { id: contract.id }, header: { "Idempotency-Key": publishKey.key } },
          body: { expected_draft_version: version, summary },
        }),
      ),
    onSuccess: async (revision) => {
      publishKey.settle();
      notify.success(`Revisão ${revision.number} publicada.`);
      await invalidate("contracts");
    },
    onError: (error) => {
      publishKey.settle(error);
    },
  });

  const editable =
    contract.can_edit &&
    contract.drafting_status !== "signed" &&
    contract.drafting_status !== "signing";

  return (
    <QueryView query={draft}>
      {(data) => {
        function handleSave(event: SubmitEvent<HTMLFormElement>) {
          event.preventDefault();
          const content = new FormData(event.currentTarget).get("content");
          save.mutate({
            content: typeof content === "string" ? content : "",
            version: data.version,
          });
        }
        function handlePublish(event: SubmitEvent<HTMLFormElement>) {
          event.preventDefault();
          publish.mutate({
            summary: formText(event.currentTarget).summary ?? "",
            version: data.version,
          });
        }
        const stale = save.error instanceof ApiError && save.error.code === "stale_version";
        return (
          <>
            <Section title="Texto do rascunho">
              <p className="muted">
                Versão de edição {data.version}, atualizada em {formatDateTime(data.updated_at)}. O
                rascunho só vale para aprovação depois de publicado como revisão.
              </p>
              {editable ? (
                <form
                  className="form form--wide"
                  onSubmit={handleSave}
                  noValidate
                  key={data.version}
                >
                  <FormError message={save.isError ? errorMessage(save.error) : null} />
                  {stale && (
                    <Button
                      variant="secondary"
                      onClick={() => {
                        void draft.refetch();
                      }}
                    >
                      Carregar a versão mais recente
                    </Button>
                  )}
                  <TextAreaField
                    label="Conteúdo"
                    name="content"
                    rows={16}
                    defaultValue={data.content ?? ""}
                    className="code"
                  />
                  <Button type="submit" variant="secondary" busy={save.isPending}>
                    Salvar rascunho
                  </Button>
                </form>
              ) : (
                <pre className="document">{data.content}</pre>
              )}
            </Section>
            {editable && (
              <Section title="Publicar revisão">
                <form className="form" onSubmit={handlePublish} noValidate>
                  <FormError message={publish.isError ? errorMessage(publish.error) : null} />
                  <TextField label="Resumo das alterações" name="summary" maxLength={200} />
                  <Button type="submit" busy={publish.isPending}>
                    Publicar rascunho salvo como revisão
                  </Button>
                </form>
              </Section>
            )}
            {editable && can("contracts.edit") && (
              <Section title="Gerar a partir de modelo">
                <GenerateFromTemplate contractId={contract.id} draftVersion={data.version} />
              </Section>
            )}
          </>
        );
      }}
    </QueryView>
  );
}
