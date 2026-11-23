import { useInfiniteQuery, useMutation } from "@tanstack/react-query";
import { useState, type SubmitEvent } from "react";

import { api, unwrap } from "../../api/client";
import { ApiError, errorMessage } from "../../api/errors";
import { cursorOf, useDirectory, useInvalidate, useOrgKey } from "../../app/queries";
import {
  CheckboxField,
  FormError,
  TextAreaField,
  TextField,
  formText,
} from "../../components/forms";
import { useNotify } from "../../components/notifications";
import { Restricted } from "../../components/Restricted";
import { EmptyState, QueryView, WhenReady } from "../../components/states";
import { Badge, Button, PageHeader, Section, Table } from "../../components/ui";
import { formatDateTime } from "../../lib/format";

function exampleRules(approverId: string) {
  return [
    {
      name: "Contratos acima de R$ 100 mil",
      conditions: [{ field: "total_value", operator: "gte", value: "100000" }],
      steps: [
        { key: "juridico", name: "Jurídico", order: 1, quorum: 1, approver_ids: [approverId] },
        { key: "diretoria", name: "Diretoria", order: 2, quorum: 1, approver_ids: [approverId] },
      ],
    },
    {
      name: "Padrão",
      conditions: [],
      steps: [
        { key: "juridico", name: "Jurídico", order: 1, quorum: 1, approver_ids: [approverId] },
      ],
    },
  ];
}

function Workflows() {
  const key = useOrgKey();
  const directory = useDirectory();
  const notify = useNotify();
  const invalidate = useInvalidate();
  const [jsonError, setJsonError] = useState<string | undefined>();
  const workflows = useInfiniteQuery({
    queryKey: key("workflows"),
    queryFn: ({ pageParam }) =>
      unwrap(api.GET("/api/v1/workflows", { params: { query: { cursor: pageParam } } })),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (page) => cursorOf(page.next),
  });
  const publish = useMutation({
    mutationFn: (body: { name: string; rules: unknown; prohibit_self_approval: boolean }) =>
      unwrap(api.POST("/api/v1/workflows", { body })),
    onSuccess: async (workflow) => {
      notify.success(
        `Workflow publicado na versão ${workflow.version}; novas rodadas usam esta versão.`,
      );
      await invalidate("workflows");
    },
  });
  const errors = publish.error instanceof ApiError ? publish.error.fieldErrors : {};
  const firstApprover = directory.data?.[0]?.user_id ?? "00000000-0000-0000-0000-000000000000";

  function handleSubmit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const values = formText(form);
    let rules: unknown;
    try {
      rules = JSON.parse(values.rules ?? "") as unknown;
    } catch {
      setJsonError("JSON inválido.");
      return;
    }
    setJsonError(undefined);
    publish.mutate({
      name: values.name ?? "",
      rules,
      prohibit_self_approval: new FormData(form).get("prohibit_self_approval") === "on",
    });
  }

  return (
    <>
      <Section title="Versões publicadas">
        <QueryView query={workflows}>
          {(data) => {
            const rows = data.pages.flatMap((page) => page.results);
            if (rows.length === 0)
              return (
                <EmptyState title="Nenhum workflow publicado; contratos ainda não podem ser enviados para aprovação." />
              );
            return (
              <Table caption="Workflows">
                <thead>
                  <tr>
                    <th scope="col">Nome</th>
                    <th scope="col">Versão</th>
                    <th scope="col">Autoaprovação</th>
                    <th scope="col">Publicado em</th>
                    <th scope="col">Situação</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((workflow) => (
                    <tr key={workflow.id}>
                      <td>{workflow.name}</td>
                      <td>{workflow.version}</td>
                      <td>
                        {workflow.prohibit_self_approval === false ? "Permitida" : "Proibida"}
                      </td>
                      <td>{formatDateTime(workflow.created_at)}</td>
                      <td>
                        {workflow.is_active ? (
                          <Badge tone="success">Ativo</Badge>
                        ) : (
                          <Badge>Histórico</Badge>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </Table>
            );
          }}
        </QueryView>
      </Section>
      <Section title="Publicar nova versão">
        <p className="muted">
          Regras avaliadas em ordem; a última deve ser a padrão, sem condições. Condições usam
          campos permitidos (sem execução de código). Aprovadores são identificados pelo id de
          usuário (veja o diretório abaixo).
        </p>
        <WhenReady queries={[directory]}>
          {() => (
            <form className="form form--wide" onSubmit={handleSubmit} noValidate>
              <FormError message={publish.isError ? errorMessage(publish.error) : null} />
              <TextField
                label="Nome"
                name="name"
                required
                defaultValue="Aprovação de contratos"
                error={errors.name}
              />
              <TextAreaField
                label="Regras (JSON)"
                name="rules"
                required
                rows={20}
                className="code"
                spellCheck={false}
                defaultValue={JSON.stringify(exampleRules(firstApprover), null, 2)}
                error={jsonError ?? errors.rules}
              />
              <CheckboxField
                label="Proibir que o autor aprove a própria revisão"
                name="prohibit_self_approval"
                defaultChecked
              />
              <Button type="submit" busy={publish.isPending}>
                Publicar
              </Button>
            </form>
          )}
        </WhenReady>
        <details>
          <summary>Diretório de membros (ids)</summary>
          <ul>
            {(directory.data ?? []).map((member) => (
              <li key={member.user_id}>
                {member.full_name || member.email}: <code className="secret">{member.user_id}</code>
              </li>
            ))}
          </ul>
        </details>
      </Section>
    </>
  );
}

export function WorkflowsPage() {
  return (
    <Restricted permission="workflows.manage">
      <PageHeader title="Workflows de aprovação" />
      <Workflows />
    </Restricted>
  );
}
