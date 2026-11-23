import { useInfiniteQuery, useMutation } from "@tanstack/react-query";
import { useState, type SubmitEvent } from "react";

import { api, unwrap, type Schemas } from "../../api/client";
import { ApiError, errorMessage } from "../../api/errors";
import { cursorOf, useDepartments, useInvalidate, useOrgKey, useTeams } from "../../app/queries";
import { useSession } from "../../auth/session";
import { Dialog } from "../../components/dialog";
import {
  CheckboxGroup,
  FormError,
  SelectField,
  TextAreaField,
  TextField,
  checkedValues,
  formText,
  textOf,
} from "../../components/forms";
import { LoadMore } from "../../components/LoadMore";
import { useNotify } from "../../components/notifications";
import { Restricted } from "../../components/Restricted";
import { EmptyState, QueryView, WhenReady } from "../../components/states";
import { Badge, Button, PageHeader, Section, Table } from "../../components/ui";
import { formatDateTime } from "../../lib/format";
import {
  INVITATION_STATUS,
  MEMBERSHIP_KIND,
  MEMBERSHIP_STATUS,
  ROLE,
  enumValue,
  options,
} from "../../lib/labels";

type Membership = Schemas["Membership"];
type Role = Schemas["RoleEnum"];

function rolesFrom(values: readonly string[]): Role[] {
  return values.flatMap((value) => {
    const role = enumValue(ROLE, value);
    return role ? [role] : [];
  });
}

function EditMemberDialog({ member, onClose }: { member: Membership; onClose: () => void }) {
  const { active } = useSession();
  const departments = useDepartments();
  const teams = useTeams();
  const notify = useNotify();
  const invalidate = useInvalidate();
  const isOwner = active?.roles.includes("owner") ?? false;
  const update = useMutation({
    mutationFn: (body: Schemas["PatchedMembershipUpdateRequest"]) =>
      unwrap(api.PATCH("/api/v1/members/{id}", { params: { path: { id: member.id } }, body })),
    onSuccess: async () => {
      notify.success("Acessos atualizados; valem na próxima requisição do usuário.");
      onClose();
      await invalidate("members");
      await invalidate("directory");
    },
  });
  const errors = update.error instanceof ApiError ? update.error.fieldErrors : {};
  const roleOptions = options(ROLE).filter((option) => isOwner || option.value !== "owner");
  return (
    <Dialog
      open
      title={`Acessos de ${member.full_name || member.email}`}
      submitLabel="Salvar"
      busy={update.isPending}
      onClose={onClose}
      onSubmit={(form) => {
        update.mutate({
          roles: rolesFrom(checkedValues(form, "roles")),
          department_ids: checkedValues(form, "department_ids"),
          team_ids: checkedValues(form, "team_ids"),
        });
      }}
    >
      <FormError message={update.isError ? errorMessage(update.error) : null} />
      <CheckboxGroup
        legend="Papéis"
        name="roles"
        options={roleOptions}
        defaultValues={member.roles}
        error={errors.roles}
      />
      <WhenReady queries={[departments, teams]}>
        {() => (
          <>
            <CheckboxGroup
              legend="Departamentos"
              name="department_ids"
              options={(departments.data ?? []).map((d) => ({ value: d.id, label: d.name }))}
              defaultValues={member.department_ids}
              error={errors.department_ids}
            />
            <CheckboxGroup
              legend="Equipes"
              name="team_ids"
              options={(teams.data ?? []).map((t) => ({ value: t.id, label: t.name }))}
              defaultValues={member.team_ids}
              error={errors.team_ids}
            />
          </>
        )}
      </WhenReady>
    </Dialog>
  );
}

function Members() {
  const key = useOrgKey();
  const { me } = useSession();
  const notify = useNotify();
  const invalidate = useInvalidate();
  const [editing, setEditing] = useState<Membership | null>(null);
  const [revoking, setRevoking] = useState<Membership | null>(null);
  const members = useInfiniteQuery({
    queryKey: key("members"),
    queryFn: ({ pageParam }) =>
      unwrap(api.GET("/api/v1/members", { params: { query: { cursor: pageParam } } })),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (page) => cursorOf(page.next),
  });
  const revoke = useMutation({
    mutationFn: ({ id, reason }: { id: string; reason: string }) =>
      unwrap(
        api.POST("/api/v1/members/{id}/revoke", { params: { path: { id } }, body: { reason } }),
      ),
    onSuccess: async () => {
      notify.success("Acesso revogado; o efeito é imediato.");
      setRevoking(null);
      await invalidate("members");
      await invalidate("directory");
    },
  });

  return (
    <Section title="Membros">
      <QueryView query={members}>
        {(data) => {
          const rows = data.pages.flatMap((page) => page.results);
          if (rows.length === 0) return <EmptyState title="Nenhum membro." />;
          return (
            <>
              <Table caption="Membros da organização">
                <thead>
                  <tr>
                    <th scope="col">Nome</th>
                    <th scope="col">E-mail</th>
                    <th scope="col">Tipo</th>
                    <th scope="col">Papéis</th>
                    <th scope="col">Situação</th>
                    <th scope="col">
                      <span className="visually-hidden">Ações</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((member) => (
                    <tr key={member.id}>
                      <td>{member.full_name || "—"}</td>
                      <td>{member.email}</td>
                      <td>{MEMBERSHIP_KIND[member.kind]}</td>
                      <td>{member.roles.map((role) => ROLE[role]).join(", ") || "—"}</td>
                      <td>
                        <Badge tone={member.status === "active" ? "success" : "neutral"}>
                          {MEMBERSHIP_STATUS[member.status]}
                        </Badge>
                      </td>
                      <td>
                        {member.status === "active" && (
                          <span className="actions">
                            <Button
                              variant="link"
                              onClick={() => {
                                setEditing(member);
                              }}
                            >
                              Editar acessos
                            </Button>
                            {member.email !== me.email && (
                              <Button
                                variant="link"
                                onClick={() => {
                                  setRevoking(member);
                                }}
                              >
                                Revogar
                              </Button>
                            )}
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </Table>
              <LoadMore query={members} />
            </>
          );
        }}
      </QueryView>
      {editing && (
        <EditMemberDialog
          member={editing}
          onClose={() => {
            setEditing(null);
          }}
        />
      )}
      <Dialog
        open={revoking !== null}
        title={`Revogar acesso de ${revoking?.full_name || revoking?.email || ""}`}
        submitLabel="Revogar acesso"
        submitVariant="danger"
        busy={revoke.isPending}
        onClose={() => {
          setRevoking(null);
        }}
        onSubmit={(form) => {
          if (revoking) revoke.mutate({ id: revoking.id, reason: textOf(form, "reason") });
        }}
      >
        <FormError message={revoke.isError ? errorMessage(revoke.error) : null} />
        <TextAreaField label="Motivo" name="reason" required rows={3} />
      </Dialog>
    </Section>
  );
}

function Invitations() {
  const key = useOrgKey();
  const { active } = useSession();
  const departments = useDepartments();
  const notify = useNotify();
  const invalidate = useInvalidate();
  const isOwner = active?.roles.includes("owner") ?? false;
  const invitations = useInfiniteQuery({
    queryKey: key("invitations"),
    queryFn: ({ pageParam }) =>
      unwrap(api.GET("/api/v1/invitations", { params: { query: { cursor: pageParam } } })),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (page) => cursorOf(page.next),
  });
  const invite = useMutation({
    mutationFn: (body: Schemas["InvitationRequest"]) =>
      unwrap(api.POST("/api/v1/invitations", { body })),
    onSuccess: async (created) => {
      notify.success(`Convite enviado para ${created.email}.`);
      await invalidate("invitations");
    },
  });
  const revoke = useMutation({
    mutationFn: (id: string) =>
      unwrap(api.POST("/api/v1/invitations/{id}/revoke", { params: { path: { id } } })),
    onSuccess: async () => {
      notify.success("Convite revogado.");
      await invalidate("invitations");
    },
  });
  const errors = invite.error instanceof ApiError ? invite.error.fieldErrors : {};

  function handleSubmit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const values = formText(form);
    invite.mutate(
      {
        email: values.email ?? "",
        kind: enumValue(MEMBERSHIP_KIND, values.kind) ?? "internal",
        roles: rolesFrom(checkedValues(form, "roles")),
        department_ids: checkedValues(form, "department_ids"),
      },
      {
        onSuccess: () => {
          form.reset();
        },
      },
    );
  }

  return (
    <Section title="Convites">
      <QueryView query={invitations}>
        {(data) => {
          const rows = data.pages.flatMap((page) => page.results);
          if (rows.length === 0) return <EmptyState title="Nenhum convite." />;
          return (
            <>
              <Table caption="Convites">
                <thead>
                  <tr>
                    <th scope="col">E-mail</th>
                    <th scope="col">Papéis</th>
                    <th scope="col">Expira em</th>
                    <th scope="col">Situação</th>
                    <th scope="col">
                      <span className="visually-hidden">Ações</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((invitation) => (
                    <tr key={invitation.id}>
                      <td>{invitation.email}</td>
                      <td>{(invitation.roles ?? []).map((role) => ROLE[role]).join(", ")}</td>
                      <td>{formatDateTime(invitation.expires_at)}</td>
                      <td>{INVITATION_STATUS[invitation.status]}</td>
                      <td>
                        {invitation.status === "pending" && (
                          <Button
                            variant="link"
                            onClick={() => {
                              revoke.mutate(invitation.id);
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
              <LoadMore query={invitations} />
            </>
          );
        }}
      </QueryView>
      <FormError message={revoke.isError ? errorMessage(revoke.error) : null} />
      <form className="form" onSubmit={handleSubmit} noValidate>
        <h3>Novo convite</h3>
        <FormError message={invite.isError ? errorMessage(invite.error) : null} />
        <TextField label="E-mail" name="email" type="email" required error={errors.email} />
        <SelectField
          label="Tipo"
          name="kind"
          defaultValue="internal"
          options={options(MEMBERSHIP_KIND)}
          hint="Participantes externos só veem contratos compartilhados com eles."
          error={errors.kind}
        />
        <CheckboxGroup
          legend="Papéis"
          name="roles"
          options={options(ROLE).filter((o) => isOwner || o.value !== "owner")}
          error={errors.roles}
        />
        <CheckboxGroup
          legend="Departamentos"
          name="department_ids"
          options={(departments.data ?? []).map((d) => ({ value: d.id, label: d.name }))}
          error={errors.department_ids}
        />
        <Button type="submit" busy={invite.isPending}>
          Enviar convite
        </Button>
      </form>
    </Section>
  );
}

export function MembersPage() {
  return (
    <Restricted permission="members.manage">
      <PageHeader
        title="Usuários e convites"
        description="Alterações e revogações valem na próxima requisição do usuário."
      />
      <Members />
      <Invitations />
    </Restricted>
  );
}
