import { useMutation, useQuery } from "@tanstack/react-query";
import type { SubmitEvent } from "react";

import { api, unwrap, unwrapEmpty } from "../../api/client";
import { errorMessage } from "../../api/errors";
import { useDirectory, useInvalidate, useMemberName, useOrgKey, useTeams } from "../../app/queries";
import { useSession } from "../../auth/session";
import { CheckboxField, FormError, SelectField, formText } from "../../components/forms";
import { useNotify } from "../../components/notifications";
import { EmptyState, QueryView } from "../../components/states";
import { Button, Section, Table } from "../../components/ui";
import { formatDateTime } from "../../lib/format";
import { useContractContext } from "./ContractLayout";

export function AccessTab() {
  const contract = useContractContext();
  const key = useOrgKey();
  const { can } = useSession();
  const notify = useNotify();
  const invalidate = useInvalidate();
  const memberName = useMemberName();
  const directory = useDirectory();
  const teams = useTeams();
  const path = { params: { path: { id: contract.id } } };
  const grants = useQuery({
    queryKey: key("contracts", "grants", contract.id),
    queryFn: () => unwrap(api.GET("/api/v1/contracts/{id}/grants", path)),
  });
  const share = useMutation({
    mutationFn: (body: { user_id?: string; team_id?: string; can_edit: boolean }) =>
      unwrap(api.POST("/api/v1/contracts/{id}/grants", { ...path, body })),
    onSuccess: async () => {
      notify.success("Acesso concedido.");
      await invalidate("contracts", "grants", contract.id);
    },
  });
  const revoke = useMutation({
    mutationFn: (grantId: string) =>
      unwrapEmpty(
        api.DELETE("/api/v1/contracts/{id}/grants/{grant_id}", {
          params: { path: { id: contract.id, grant_id: grantId } },
        }),
      ),
    onSuccess: async () => {
      notify.success("Acesso removido.");
      await invalidate("contracts", "grants", contract.id);
    },
  });
  const teamName = (teamId: string | null) =>
    teams.data?.find((team) => team.id === teamId)?.name ?? "Equipe";

  function handleSubmit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const values = formText(form);
    const [kind, id] = (values.target ?? "").split(":");
    if (!id) return;
    const canEdit = new FormData(form).get("can_edit") === "on";
    share.mutate(
      kind === "team" ? { team_id: id, can_edit: canEdit } : { user_id: id, can_edit: canEdit },
    );
  }

  return (
    <>
      <Section title="Quem tem acesso concedido">
        <p className="muted">
          Além destes acessos, a visibilidade segue a confidencialidade, o departamento e os papéis.
          Aprovadores recebem acesso somente enquanto a rodada estiver aberta.
        </p>
        <QueryView query={grants}>
          {(items) =>
            items.length === 0 ? (
              <EmptyState title="Nenhum acesso concedido individualmente." />
            ) : (
              <Table caption="Acessos concedidos">
                <thead>
                  <tr>
                    <th scope="col">Para</th>
                    <th scope="col">Pode editar</th>
                    <th scope="col">Origem</th>
                    <th scope="col">Desde</th>
                    <th scope="col">
                      <span className="visually-hidden">Ações</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((grant) => (
                    <tr key={grant.id}>
                      <td>{grant.user_id ? memberName(grant.user_id) : teamName(grant.team_id)}</td>
                      <td>{grant.can_edit ? "Sim" : "Não"}</td>
                      <td>{grant.source === "approval" ? "Rodada de aprovação" : "Manual"}</td>
                      <td>{formatDateTime(grant.created_at)}</td>
                      <td>
                        {grant.source === "manual" && can("contracts.share") && (
                          <Button
                            variant="link"
                            onClick={() => {
                              revoke.mutate(grant.id);
                            }}
                          >
                            Remover
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
      {can("contracts.share") && (
        <Section title="Conceder acesso">
          <form className="form" onSubmit={handleSubmit} noValidate>
            <FormError message={share.isError ? errorMessage(share.error) : null} />
            <SelectField
              label="Pessoa ou equipe"
              name="target"
              required
              placeholder="Selecione"
              options={[
                ...(directory.data ?? []).map((m) => ({
                  value: `user:${m.user_id}`,
                  label: m.full_name || m.email,
                })),
                ...(teams.data ?? []).map((t) => ({
                  value: `team:${t.id}`,
                  label: `Equipe: ${t.name}`,
                })),
              ]}
            />
            <CheckboxField label="Pode editar" name="can_edit" />
            <Button type="submit" busy={share.isPending}>
              Conceder
            </Button>
          </form>
        </Section>
      )}
    </>
  );
}
