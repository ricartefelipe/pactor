import { useMutation, useQuery } from "@tanstack/react-query";
import { useState, type SubmitEvent } from "react";

import { api, unwrap, type Schemas } from "../../api/client";
import { ApiError, errorMessage } from "../../api/errors";
import { useInvalidate, useOrgKey } from "../../app/queries";
import {
  CheckboxGroup,
  FormError,
  TextField,
  checkedValues,
  formText,
} from "../../components/forms";
import { useNotify } from "../../components/notifications";
import { Restricted } from "../../components/Restricted";
import { EmptyState, QueryView } from "../../components/states";
import { Badge, Button, PageHeader, Section, Table } from "../../components/ui";
import { formatDateTime } from "../../lib/format";
import { ROLE, SCOPE, enumValue, labelOf, options } from "../../lib/labels";

/** Papéis operacionais: integrações nunca recebem gestão da organização. */
const INTEGRATION_ROLES = options(ROLE).filter((o) => o.value !== "owner" && o.value !== "admin");

function Integrations() {
  const key = useOrgKey();
  const notify = useNotify();
  const invalidate = useInvalidate();
  const [issued, setIssued] = useState<Schemas["Issued"] | null>(null);
  const integrations = useQuery({
    queryKey: key("integrations"),
    queryFn: () => unwrap(api.GET("/api/v1/integrations")),
  });
  const create = useMutation({
    mutationFn: (body: Schemas["IntegrationCreateRequest"]) =>
      unwrap(api.POST("/api/v1/integrations", { body })),
    onSuccess: async (created) => {
      setIssued(created);
      await invalidate("integrations");
    },
  });
  const revoke = useMutation({
    mutationFn: (id: string) =>
      unwrap(api.POST("/api/v1/integrations/{id}/revoke", { params: { path: { id } } })),
    onSuccess: async () => {
      notify.success("Integração revogada; os tokens emitidos deixam de valer.");
      await invalidate("integrations");
    },
  });
  const errors = create.error instanceof ApiError ? create.error.fieldErrors : {};

  function handleSubmit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const roles = checkedValues(form, "roles").flatMap((v) => {
      const role = enumValue(ROLE, v);
      return role ? [role] : [];
    });
    const scopes = checkedValues(form, "scopes").flatMap((v) => {
      const scope = enumValue(SCOPE, v);
      return scope ? [scope] : [];
    });
    create.mutate({ name: formText(form).name ?? "", roles, scopes });
  }

  return (
    <>
      {issued && (
        <Section title="Credenciais emitidas">
          <div className="callout" role="alert">
            <p>
              Copie o segredo agora: ele <strong>não será exibido novamente</strong>. Obtenha tokens
              em <code>/o/token/</code> com o fluxo client credentials.
            </p>
            <p>
              client_id: <code className="secret">{issued.client_id}</code>
            </p>
            <p>
              client_secret: <code className="secret">{issued.client_secret}</code>
            </p>
            <Button
              variant="secondary"
              onClick={() => {
                setIssued(null);
              }}
            >
              Já copiei
            </Button>
          </div>
        </Section>
      )}
      <Section title="Integrações">
        <QueryView query={integrations}>
          {(items) =>
            items.length === 0 ? (
              <EmptyState title="Nenhuma integração." />
            ) : (
              <Table caption="Integrações">
                <thead>
                  <tr>
                    <th scope="col">Nome</th>
                    <th scope="col">client_id</th>
                    <th scope="col">Papéis</th>
                    <th scope="col">Escopos</th>
                    <th scope="col">Criada em</th>
                    <th scope="col">Situação</th>
                    <th scope="col">
                      <span className="visually-hidden">Ações</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((integration) => (
                    <tr key={integration.id}>
                      <td>{integration.name}</td>
                      <td>
                        <code className="secret">{integration.client_id}</code>
                      </td>
                      <td>{integration.roles.map((r) => ROLE[r]).join(", ")}</td>
                      <td>{integration.scopes.map((s) => labelOf(SCOPE, s)).join(", ")}</td>
                      <td>{formatDateTime(integration.created_at)}</td>
                      <td>
                        {integration.revoked_at ? (
                          <Badge>Revogada</Badge>
                        ) : (
                          <Badge tone="success">Ativa</Badge>
                        )}
                      </td>
                      <td>
                        {!integration.revoked_at && (
                          <Button
                            variant="link"
                            onClick={() => {
                              revoke.mutate(integration.id);
                            }}
                          >
                            Revogar
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
        <FormError message={revoke.isError ? errorMessage(revoke.error) : null} />
      </Section>
      <Section title="Nova integração">
        <p className="muted">
          O token concede no máximo a interseção entre os papéis e os escopos escolhidos; a
          visibilidade por contrato continua valendo.
        </p>
        <form className="form" onSubmit={handleSubmit} noValidate>
          <FormError message={create.isError ? errorMessage(create.error) : null} />
          <TextField label="Nome" name="name" required maxLength={120} error={errors.name} />
          <CheckboxGroup
            legend="Papéis"
            name="roles"
            options={INTEGRATION_ROLES}
            error={errors.roles}
          />
          <CheckboxGroup
            legend="Escopos"
            name="scopes"
            options={options(SCOPE)}
            error={errors.scopes}
          />
          <Button type="submit" busy={create.isPending}>
            Emitir credenciais
          </Button>
        </form>
      </Section>
    </>
  );
}

export function IntegrationsPage() {
  return (
    <Restricted permission="integrations.manage">
      <PageHeader
        title="Integrações"
        description="Acesso máquina a máquina pela API, vinculado a esta organização."
      />
      <Integrations />
    </Restricted>
  );
}
