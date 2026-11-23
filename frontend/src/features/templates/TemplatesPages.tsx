import { useMutation, useQuery } from "@tanstack/react-query";
import { useState, type SubmitEvent } from "react";
import { Link, useNavigate, useParams } from "react-router";

import { api, unwrap, type Schemas } from "../../api/client";
import { ApiError, errorMessage } from "../../api/errors";
import { useInvalidate, useMemberName, useOrgKey } from "../../app/queries";
import { useSession } from "../../auth/session";
import { FormError, SelectField, TextAreaField, TextField, formText } from "../../components/forms";
import { useNotify } from "../../components/notifications";
import { Restricted } from "../../components/Restricted";
import { EmptyState, QueryView } from "../../components/states";
import { Badge, Button, PageHeader, Section, Table } from "../../components/ui";
import { formatDateTime } from "../../lib/format";
import { CONTRACT_TYPE, enumValue, options } from "../../lib/labels";
import { EXAMPLE_DEFINITION } from "../../lib/templateDefinition";

/** Lê o JSON digitado; erro de sintaxe vira mensagem no próprio campo. */
function parseJson(text: string): { value: unknown } | { error: string } {
  try {
    return { value: JSON.parse(text) as unknown };
  } catch {
    return { error: "JSON inválido. Confira vírgulas, aspas e chaves." };
  }
}

export function useTemplates() {
  const key = useOrgKey();
  return useQuery({
    queryKey: key("templates"),
    queryFn: () => unwrap(api.GET("/api/v1/templates")),
  });
}

export function TemplatesPage() {
  const templates = useTemplates();
  const { can } = useSession();
  return (
    <Restricted permission="contracts.read">
      <PageHeader
        title="Modelos de contrato"
        description="Modelos versionados com campos tipados; cada revisão gerada registra a versão usada."
        actions={
          can("templates.manage") && (
            <Link className="button button--primary" to="/modelos/novo">
              Novo modelo
            </Link>
          )
        }
      />
      <QueryView query={templates}>
        {(items) =>
          items.length === 0 ? (
            <EmptyState title="Nenhum modelo cadastrado." />
          ) : (
            <Table caption="Modelos">
              <thead>
                <tr>
                  <th scope="col">Nome</th>
                  <th scope="col">Tipo</th>
                  <th scope="col">Versões publicadas</th>
                  <th scope="col">Atualizado em</th>
                </tr>
              </thead>
              <tbody>
                {items.map((template) => (
                  <tr key={template.id}>
                    <td>
                      <Link to={`/modelos/${template.id}`}>{template.name}</Link>
                    </td>
                    <td>{CONTRACT_TYPE[template.contract_type]}</td>
                    <td>{template.versions.length}</td>
                    <td>{formatDateTime(template.updated_at)}</td>
                  </tr>
                ))}
              </tbody>
            </Table>
          )
        }
      </QueryView>
    </Restricted>
  );
}

function NewTemplateForm() {
  const navigate = useNavigate();
  const notify = useNotify();
  const invalidate = useInvalidate();
  const [jsonError, setJsonError] = useState<string | undefined>();
  const create = useMutation({
    mutationFn: (body: Schemas["TemplateCreateRequest"]) =>
      unwrap(api.POST("/api/v1/templates", { body })),
    onSuccess: async (template) => {
      notify.success("Modelo criado. Revise o rascunho e publique a primeira versão.");
      await invalidate("templates");
      await navigate(`/modelos/${template.id}`);
    },
  });
  const errors = create.error instanceof ApiError ? create.error.fieldErrors : {};

  function handleSubmit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    const values = formText(event.currentTarget);
    const contractType = enumValue(CONTRACT_TYPE, values.contract_type);
    const parsed = parseJson(values.definition ?? "");
    if ("error" in parsed) {
      setJsonError(parsed.error);
      return;
    }
    setJsonError(undefined);
    if (contractType === undefined) return;
    create.mutate({
      name: values.name ?? "",
      contract_type: contractType,
      description: values.description ?? "",
      definition: parsed.value,
    });
  }

  return (
    <>
      <PageHeader title="Novo modelo" />
      <form className="form form--wide" onSubmit={handleSubmit} noValidate>
        <FormError message={create.isError ? errorMessage(create.error) : null} />
        <TextField label="Nome" name="name" required maxLength={160} error={errors.name} />
        <SelectField
          label="Tipo de contrato"
          name="contract_type"
          required
          options={options(CONTRACT_TYPE)}
          error={errors.contract_type}
        />
        <TextAreaField label="Descrição" name="description" rows={2} error={errors.description} />
        <TextAreaField
          label="Definição (JSON)"
          name="definition"
          required
          rows={18}
          className="code"
          spellCheck={false}
          defaultValue={JSON.stringify(EXAMPLE_DEFINITION, null, 2)}
          hint="Campos tipados (text, date, decimal, boolean, choice) e seções com marcadores {{chave}}. Nenhum código é executado."
          error={jsonError ?? errors.definition}
        />
        <Button type="submit" busy={create.isPending}>
          Criar modelo
        </Button>
      </form>
    </>
  );
}

export function NewTemplatePage() {
  return (
    <Restricted permission="templates.manage">
      <NewTemplateForm />
    </Restricted>
  );
}

function TemplateEditor({ template }: { template: Schemas["Template"] }) {
  const notify = useNotify();
  const invalidate = useInvalidate();
  const [jsonError, setJsonError] = useState<string | undefined>();
  const path = { params: { path: { id: template.id } } };
  const save = useMutation({
    mutationFn: (definition: unknown) =>
      unwrap(
        api.PUT("/api/v1/templates/{id}/draft", {
          ...path,
          body: { definition, expected_version: template.draft_version },
        }),
      ),
    onSuccess: async () => {
      notify.success("Rascunho do modelo salvo.");
      await invalidate("templates");
    },
  });
  const publish = useMutation({
    mutationFn: (changeNote: string) =>
      unwrap(
        api.POST("/api/v1/templates/{id}/publish", {
          ...path,
          body: { expected_version: template.draft_version, change_note: changeNote },
        }),
      ),
    onSuccess: async (version) => {
      notify.success(`Versão ${version.number} publicada.`);
      await invalidate("templates");
    },
  });

  function handleSave(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    const parsed = parseJson(formText(event.currentTarget).definition ?? "");
    if ("error" in parsed) {
      setJsonError(parsed.error);
      return;
    }
    setJsonError(undefined);
    save.mutate(parsed.value);
  }

  function handlePublish(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    publish.mutate(formText(event.currentTarget).change_note ?? "");
  }

  const saveErrors = save.error instanceof ApiError ? save.error.fieldErrors : {};
  return (
    <>
      <Section title={`Rascunho (versão de edição ${template.draft_version})`}>
        <form
          className="form form--wide"
          onSubmit={handleSave}
          noValidate
          key={template.draft_version}
        >
          <FormError message={save.isError ? errorMessage(save.error) : null} />
          <TextAreaField
            label="Definição (JSON)"
            name="definition"
            required
            rows={18}
            className="code"
            spellCheck={false}
            defaultValue={JSON.stringify(template.draft_definition, null, 2)}
            error={jsonError ?? saveErrors.definition}
          />
          <Button type="submit" variant="secondary" busy={save.isPending}>
            Salvar rascunho
          </Button>
        </form>
      </Section>
      <Section title="Publicar">
        <form className="form" onSubmit={handlePublish} noValidate>
          <FormError message={publish.isError ? errorMessage(publish.error) : null} />
          <TextField label="Nota da versão" name="change_note" maxLength={200} />
          <Button type="submit" busy={publish.isPending}>
            Publicar rascunho como nova versão
          </Button>
        </form>
      </Section>
    </>
  );
}

export function TemplatePage() {
  const { id = "" } = useParams();
  const key = useOrgKey();
  const { can } = useSession();
  const memberName = useMemberName();
  const detail = useQuery({
    queryKey: key("templates", "detail", id),
    queryFn: () => unwrap(api.GET("/api/v1/templates/{id}", { params: { path: { id } } })),
  });
  return (
    <Restricted permission="contracts.read">
      <QueryView query={detail}>
        {(template) => (
          <>
            <PageHeader
              title={template.name}
              description={
                <>
                  {CONTRACT_TYPE[template.contract_type]}{" "}
                  <Badge tone={template.is_active ? "success" : "neutral"}>
                    {template.is_active ? "Ativo" : "Inativo"}
                  </Badge>
                </>
              }
            />
            {template.description && <p>{template.description}</p>}
            <Section title="Versões publicadas">
              {template.versions.length === 0 ? (
                <EmptyState title="Nenhuma versão publicada; contratos ainda não podem usar este modelo." />
              ) : (
                <Table caption="Versões publicadas">
                  <thead>
                    <tr>
                      <th scope="col">Versão</th>
                      <th scope="col">Nota</th>
                      <th scope="col">Publicada por</th>
                      <th scope="col">Data</th>
                    </tr>
                  </thead>
                  <tbody>
                    {template.versions.map((version) => (
                      <tr key={version.id}>
                        <td>{version.number}</td>
                        <td>{version.change_note || "—"}</td>
                        <td>{memberName(version.published_by_id)}</td>
                        <td>{formatDateTime(version.created_at)}</td>
                      </tr>
                    ))}
                  </tbody>
                </Table>
              )}
            </Section>
            {can("templates.manage") && <TemplateEditor template={template} />}
          </>
        )}
      </QueryView>
    </Restricted>
  );
}
