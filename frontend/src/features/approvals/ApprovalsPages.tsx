import { useMutation, useQuery } from "@tanstack/react-query";
import type { SubmitEvent } from "react";
import { Link, useParams } from "react-router";

import { api, unwrap, type Schemas } from "../../api/client";
import { ApiError, errorMessage } from "../../api/errors";
import { useDirectory, useInvalidate, useMemberName, useOrgKey } from "../../app/queries";
import { useSession } from "../../auth/session";
import { FormError, SelectField, TextAreaField, TextField, formText } from "../../components/forms";
import { useNotify } from "../../components/notifications";
import { Restricted } from "../../components/Restricted";
import { EmptyState, QueryView } from "../../components/states";
import { Badge, Button, PageHeader, Section, Table } from "../../components/ui";
import { formatDateTime } from "../../lib/format";
import { useIdempotencyKey } from "../../lib/idempotency";
import { DECISION, enumValue, options } from "../../lib/labels";
import { RoundStatusBadge, RoundView } from "./RoundView";

function useDelegations() {
  const key = useOrgKey();
  return useQuery({
    queryKey: key("delegations"),
    queryFn: () => unwrap(api.GET("/api/v1/delegations")),
  });
}

function isActive(delegation: Schemas["Delegation"], now: Date): boolean {
  return (
    delegation.revoked_at === null &&
    new Date(delegation.starts_at) <= now &&
    now < new Date(delegation.ends_at)
  );
}

function Inbox() {
  const key = useOrgKey();
  const inbox = useQuery({
    queryKey: key("approvals", "inbox"),
    queryFn: () => unwrap(api.GET("/api/v1/approval-rounds/inbox")),
  });
  return (
    <Section title="Aguardando seu voto">
      <QueryView query={inbox}>
        {(rounds) =>
          rounds.length === 0 ? (
            <EmptyState title="Nenhuma aprovação pendente para você." />
          ) : (
            <Table caption="Aprovações pendentes">
              <thead>
                <tr>
                  <th scope="col">Contrato</th>
                  <th scope="col">Revisão</th>
                  <th scope="col">Aberta em</th>
                  <th scope="col">Situação</th>
                </tr>
              </thead>
              <tbody>
                {rounds.map((round) => (
                  <tr key={round.id}>
                    <td>
                      <Link to={`/aprovacoes/${round.id}`}>
                        {round.contract_reference} · {round.contract_title}
                      </Link>
                    </td>
                    <td>{round.revision_number}</td>
                    <td>{formatDateTime(round.created_at)}</td>
                    <td>
                      <RoundStatusBadge status={round.status} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </Table>
          )
        }
      </QueryView>
    </Section>
  );
}

function Delegations() {
  const delegations = useDelegations();
  const directory = useDirectory();
  const memberName = useMemberName();
  const { me } = useSession();
  const notify = useNotify();
  const invalidate = useInvalidate();
  const create = useMutation({
    mutationFn: (body: Schemas["DelegationRequest"]) =>
      unwrap(api.POST("/api/v1/delegations", { body })),
    onSuccess: async () => {
      notify.success("Delegação registrada.");
      await invalidate("delegations");
    },
  });
  const revoke = useMutation({
    mutationFn: (id: string) =>
      unwrap(api.POST("/api/v1/delegations/{id}/revoke", { params: { path: { id } } })),
    onSuccess: async () => {
      notify.success("Delegação revogada.");
      await invalidate("delegations");
    },
  });
  const errors = create.error instanceof ApiError ? create.error.fieldErrors : {};

  function handleSubmit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    const values = formText(event.currentTarget);
    create.mutate({
      delegate_id: values.delegate_id ?? "",
      starts_at: new Date(values.starts_at ?? "").toISOString(),
      ends_at: new Date(values.ends_at ?? "").toISOString(),
      reason: values.reason ?? "",
    });
  }

  const now = new Date();
  return (
    <Section title="Delegações">
      <QueryView query={delegations}>
        {(items) =>
          items.length === 0 ? (
            <EmptyState title="Nenhuma delegação." />
          ) : (
            <Table caption="Delegações">
              <thead>
                <tr>
                  <th scope="col">De</th>
                  <th scope="col">Para</th>
                  <th scope="col">Período</th>
                  <th scope="col">Motivo</th>
                  <th scope="col">Situação</th>
                  <th scope="col">
                    <span className="visually-hidden">Ações</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {items.map((delegation) => (
                  <tr key={delegation.id}>
                    <td>{memberName(delegation.delegator_id)}</td>
                    <td>{memberName(delegation.delegate_id)}</td>
                    <td>
                      {formatDateTime(delegation.starts_at)} a {formatDateTime(delegation.ends_at)}
                    </td>
                    <td>{delegation.reason}</td>
                    <td>
                      {delegation.revoked_at ? (
                        <Badge>Revogada</Badge>
                      ) : isActive(delegation, now) ? (
                        <Badge tone="success">Vigente</Badge>
                      ) : (
                        <Badge tone="neutral">Fora do período</Badge>
                      )}
                    </td>
                    <td>
                      {!delegation.revoked_at && delegation.delegator_id === me.id && (
                        <Button
                          variant="link"
                          onClick={() => {
                            revoke.mutate(delegation.id);
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
      <form className="form" onSubmit={handleSubmit} noValidate>
        <h3>Delegar meus votos</h3>
        <FormError message={create.isError ? errorMessage(create.error) : null} />
        <SelectField
          label="Delegado"
          name="delegate_id"
          required
          placeholder="Selecione"
          options={(directory.data ?? [])
            .filter((m) => m.user_id !== me.id)
            .map((m) => ({ value: m.user_id, label: m.full_name || m.email }))}
          error={errors.delegate_id}
        />
        <TextField
          label="Início"
          name="starts_at"
          type="datetime-local"
          required
          error={errors.starts_at}
        />
        <TextField
          label="Fim"
          name="ends_at"
          type="datetime-local"
          required
          error={errors.ends_at}
        />
        <TextField label="Motivo" name="reason" required error={errors.reason} />
        <Button type="submit" busy={create.isPending}>
          Delegar
        </Button>
      </form>
    </Section>
  );
}

export function ApprovalsPage() {
  return (
    <Restricted permission="approvals.vote">
      <PageHeader
        title="Aprovações"
        description="Votos vinculados à revisão e ao conteúdo exatos."
      />
      <Inbox />
      <Delegations />
    </Restricted>
  );
}

function VoteForm({ round }: { round: Schemas["Round"] }) {
  const { me } = useSession();
  const memberName = useMemberName();
  const delegations = useDelegations();
  const notify = useNotify();
  const invalidate = useInvalidate();
  const idempotency = useIdempotencyKey();
  const vote = useMutation({
    mutationFn: (body: Schemas["VoteCreateRequest"]) =>
      unwrap(
        api.POST("/api/v1/approval-rounds/{id}/vote", {
          params: { path: { id: round.id }, header: { "Idempotency-Key": idempotency.key } },
          body,
        }),
      ),
    onSuccess: async () => {
      idempotency.settle();
      notify.success("Voto registrado.");
      await invalidate("approvals");
      await invalidate("contracts");
    },
    onError: (error) => {
      idempotency.settle(error);
    },
  });

  const now = new Date();
  const delegators = new Set(
    (delegations.data ?? [])
      .filter((d) => d.delegate_id === me.id && isActive(d, now))
      .map((d) => d.delegator_id),
  );
  // Vagas em que posso votar: como aprovador ou em nome de quem delegou para mim.
  const slots = round.steps
    .filter((step) => step.state === "active")
    .flatMap((step) =>
      step.approvers
        .filter((a) => a.active && (a.user_id === me.id || delegators.has(a.user_id)))
        .filter(
          (a) => !round.votes.some((v) => v.step_key === step.key && v.approver_id === a.user_id),
        )
        .map((a) => ({ step, approverId: a.user_id })),
    );

  if (slots.length === 0) {
    return <p>Não há etapa ativa aguardando o seu voto nesta rodada.</p>;
  }

  function handleSubmit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    const values = formText(event.currentTarget);
    const decision = enumValue(DECISION, values.decision);
    const slot = slots[Number(values.slot ?? "0")];
    if (!decision || !slot) return;
    vote.mutate({
      step_key: slot.step.key,
      decision,
      expected_fingerprint: round.fingerprint,
      ...(slot.approverId !== me.id ? { on_behalf_of: slot.approverId } : {}),
      ...(values.comment ? { comment: values.comment } : {}),
    });
  }

  return (
    <form className="form" onSubmit={handleSubmit} noValidate>
      <FormError message={vote.isError ? errorMessage(vote.error) : null} />
      <SelectField
        label="Etapa"
        name="slot"
        required
        options={slots.map((slot, index) => ({
          value: String(index),
          label:
            slot.approverId === me.id
              ? slot.step.name
              : `${slot.step.name} — em nome de ${memberName(slot.approverId)}`,
        }))}
      />
      <SelectField label="Decisão" name="decision" required options={options(DECISION)} />
      <TextAreaField
        label="Comentário"
        name="comment"
        rows={3}
        hint="Obrigatório para rejeitar ou pedir ajustes."
      />
      <Button type="submit" busy={vote.isPending}>
        Registrar voto
      </Button>
    </form>
  );
}

export function RoundPage() {
  const { id = "" } = useParams();
  const key = useOrgKey();
  const round = useQuery({
    queryKey: key("approvals", "round", id),
    queryFn: () => unwrap(api.GET("/api/v1/approval-rounds/{id}", { params: { path: { id } } })),
  });
  const inbox = useQuery({
    queryKey: key("approvals", "inbox"),
    queryFn: () => unwrap(api.GET("/api/v1/approval-rounds/inbox")),
  });
  const content = inbox.data?.find((item) => item.id === id)?.revision_content;

  return (
    <Restricted permission="approvals.vote">
      <QueryView query={round}>
        {(data) => (
          <>
            <PageHeader
              title={`Aprovação: ${data.contract_reference} · ${data.contract_title}`}
              description={
                <Link to={`/contratos/${data.contract_id}/revisoes?revisao=${data.revision_id}`}>
                  Ver contrato
                </Link>
              }
            />
            <RoundView round={data} />
            {content !== undefined && (
              <Section title={`Conteúdo da revisão ${data.revision_number}`}>
                <pre className="document">{content}</pre>
              </Section>
            )}
            {data.status === "open" && (
              <Section title="Seu voto">
                <VoteForm round={data} />
              </Section>
            )}
          </>
        )}
      </QueryView>
    </Restricted>
  );
}
