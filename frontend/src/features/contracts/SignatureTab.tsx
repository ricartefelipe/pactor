import { useMutation, useQuery } from "@tanstack/react-query";
import { useState, type SubmitEvent } from "react";

import { api, unwrap, type Schemas } from "../../api/client";
import { sameOrigin, withCsrf } from "../../api/csrf";
import { errorMessage, networkError, toApiError } from "../../api/errors";
import { useInvalidate, useOrgKey } from "../../app/queries";
import { useSession } from "../../auth/session";
import { Dialog } from "../../components/dialog";
import { FormError, TextAreaField, TextField, textOf } from "../../components/forms";
import { useNotify } from "../../components/notifications";
import { EmptyState, QueryView } from "../../components/states";
import { Badge, Button, Section, Table } from "../../components/ui";
import { formatBytes, formatDateTime } from "../../lib/format";
import { useIdempotencyKey } from "../../lib/idempotency";
import { ENVELOPE_STATE, RECIPIENT_STATUS } from "../../lib/labels";
import { useContractContext } from "./ContractLayout";

type Envelope = Schemas["Envelope"];
type Action = Schemas["ActionEnum"];

const IN_FLIGHT: ReadonlySet<Schemas["EnvelopeStateEnum"]> = new Set([
  "pending_dispatch",
  "dispatch_unknown",
  "voiding",
]);
const TERMINAL: ReadonlySet<Schemas["EnvelopeStateEnum"]> = new Set([
  "completed",
  "declined",
  "voided",
  "failed",
]);

const OUTCOMES: Readonly<Record<string, string>> = {
  applied: "aplicado",
  pending: "recebido, aguardando processamento",
  stale: "ignorado (fora de ordem)",
  old_envelope: "ignorado (envelope substituído)",
  unknown_envelope: "ignorado (envelope desconhecido)",
};

interface Delivery {
  outcome: string;
  duplicate: boolean;
}

function isDelivery(value: unknown): value is Delivery {
  return (
    typeof value === "object" &&
    value !== null &&
    "outcome" in value &&
    typeof value.outcome === "string" &&
    "duplicate" in value &&
    typeof value.duplicate === "boolean"
  );
}

/** Entrega ao endpoint público de webhooks o evento assinado gerado pelo simulador. */
async function deliverWebhook(webhook: Schemas["SimulatedWebhook"]): Promise<Delivery> {
  let response: Response;
  try {
    response = await fetch(
      withCsrf(
        new Request(sameOrigin("/webhooks/signatures/simulator"), {
          method: "POST",
          credentials: "same-origin",
          headers: { ...webhook.headers, "Content-Type": "application/json" },
          body: webhook.body,
        }),
      ),
    );
  } catch {
    throw networkError();
  }
  const payload: unknown = await response.json();
  if (!response.ok || !isDelivery(payload)) throw toApiError(response.status, payload);
  return payload;
}

function SimulatorPanel({ envelope }: { envelope: Envelope }) {
  const notify = useNotify();
  const invalidate = useInvalidate();
  const [last, setLast] = useState<string | null>(null);
  const act = useMutation({
    mutationFn: async ({ recipientKey, action }: { recipientKey: string; action: Action }) => {
      const webhook = await unwrap(
        api.POST("/api/v1/envelopes/{id}/simulate", {
          params: { path: { id: envelope.id } },
          body: { recipient_key: recipientKey, action },
        }),
      );
      return deliverWebhook(webhook);
    },
    onSuccess: async (delivery) => {
      const text = delivery.duplicate
        ? "Evento repetido reconhecido como duplicado: nenhum efeito novo."
        : `Evento entregue por webhook: ${OUTCOMES[delivery.outcome] ?? delivery.outcome}.`;
      setLast(text);
      notify.success(text);
      await invalidate("contracts");
    },
  });
  const pending = envelope.recipients.filter(
    (r) => r.status !== "signed" && r.status !== "declined",
  );

  return (
    <div className="simulator">
      <h3>Simulador de assinatura</h3>
      <p className="muted">
        Ambiente de demonstração: cada ação gera um webhook assinado (HMAC) que percorre o mesmo
        endpoint usado pelos provedores reais.
      </p>
      <FormError message={act.isError ? errorMessage(act.error) : null} />
      <div className="actions">
        {!TERMINAL.has(envelope.state) &&
          pending.map((recipient) => (
            <span key={recipient.recipient_key} className="actions">
              <Button
                busy={act.isPending}
                onClick={() => {
                  act.mutate({ recipientKey: recipient.recipient_key, action: "sign" });
                }}
              >
                Assinar como {recipient.name}
              </Button>
              <Button
                variant="secondary"
                busy={act.isPending}
                onClick={() => {
                  act.mutate({ recipientKey: recipient.recipient_key, action: "decline" });
                }}
              >
                Recusar como {recipient.name}
              </Button>
            </span>
          ))}
        <Button
          variant="secondary"
          busy={act.isPending}
          onClick={() => {
            act.mutate({
              recipientKey: envelope.recipients[0]?.recipient_key ?? "",
              action: "replay",
            });
          }}
        >
          Reenviar último evento
        </Button>
      </div>
      {last && (
        <p role="status" className="muted">
          {last}
        </p>
      )}
    </div>
  );
}

function EnvelopeCard({ envelope }: { envelope: Envelope }) {
  const { can } = useSession();
  const notify = useNotify();
  const invalidate = useInvalidate();
  const [voiding, setVoiding] = useState(false);
  const voidEnvelope = useMutation({
    mutationFn: (reason: string) =>
      unwrap(
        api.POST("/api/v1/envelopes/{id}/void", {
          params: { path: { id: envelope.id } },
          body: { reason },
        }),
      ),
    onSuccess: async () => {
      setVoiding(false);
      notify.success("Cancelamento do envelope solicitado.");
      await invalidate("contracts");
    },
  });

  return (
    <article className="card">
      <h3>
        Envelope da revisão {envelope.revision_number} ·{" "}
        <Badge
          tone={
            envelope.state === "completed"
              ? "success"
              : TERMINAL.has(envelope.state)
                ? "danger"
                : "warning"
          }
        >
          {ENVELOPE_STATE[envelope.state]}
        </Badge>
      </h3>
      <p className="muted">
        {envelope.subject} · criado em {formatDateTime(envelope.created_at)}
        {envelope.completed_at && <> · concluído em {formatDateTime(envelope.completed_at)}</>}
        {envelope.last_error && <> · último erro: {envelope.last_error}</>}
      </p>
      <Table caption="Signatários">
        <thead>
          <tr>
            <th scope="col">Ordem</th>
            <th scope="col">Nome</th>
            <th scope="col">E-mail</th>
            <th scope="col">Situação</th>
          </tr>
        </thead>
        <tbody>
          {envelope.recipients.map((recipient) => (
            <tr key={recipient.recipient_key}>
              <td>{recipient.routing_order}</td>
              <td>{recipient.name}</td>
              <td>{recipient.email}</td>
              <td>{RECIPIENT_STATUS[recipient.status]}</td>
            </tr>
          ))}
        </tbody>
      </Table>
      {envelope.documents.length > 0 && (
        <ul>
          {envelope.documents.map((document) => (
            <li key={document.id}>
              <a href={`/api/v1/envelopes/${envelope.id}/documents/${document.id}`}>
                {document.name}
              </a>{" "}
              ({formatBytes(document.size)}, SHA-256{" "}
              <code className="secret">{document.sha256.slice(0, 12)}…</code>)
            </li>
          ))}
        </ul>
      )}
      {envelope.provider === "simulator" && envelope.provider_envelope_id && (
        <SimulatorPanel envelope={envelope} />
      )}
      {can("signatures.send") && (envelope.state === "sent" || envelope.state === "delivered") && (
        <Button
          variant="danger"
          onClick={() => {
            setVoiding(true);
          }}
        >
          Cancelar envelope
        </Button>
      )}
      <Dialog
        open={voiding}
        title="Cancelar envelope"
        submitLabel="Cancelar envelope"
        submitVariant="danger"
        busy={voidEnvelope.isPending}
        onClose={() => {
          setVoiding(false);
        }}
        onSubmit={(form) => {
          voidEnvelope.mutate(textOf(form, "reason"));
        }}
      >
        <FormError message={voidEnvelope.isError ? errorMessage(voidEnvelope.error) : null} />
        <TextAreaField label="Motivo" name="reason" required rows={3} />
      </Dialog>
    </article>
  );
}

interface SignerRow {
  id: number;
}

function SendForm({ contractId, fingerprint }: { contractId: string; fingerprint: string }) {
  const notify = useNotify();
  const invalidate = useInvalidate();
  const idempotency = useIdempotencyKey();
  const [rows, setRows] = useState<SignerRow[]>([{ id: 1 }, { id: 2 }]);
  const send = useMutation({
    mutationFn: (signers: Schemas["SignerRequest"][]) =>
      unwrap(
        api.POST("/api/v1/envelopes/send", {
          params: { header: { "Idempotency-Key": idempotency.key } },
          body: { contract_id: contractId, expected_fingerprint: fingerprint, signers },
        }),
      ),
    onSuccess: async () => {
      idempotency.settle();
      notify.success("Envelope criado; o envio ao provedor acontece em segundo plano.");
      await invalidate("contracts");
    },
    onError: (error) => {
      idempotency.settle(error);
    },
  });

  function handleSubmit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const signers = rows.map((row) => ({
      name: textOf(form, `name-${row.id}`).trim(),
      email: textOf(form, `email-${row.id}`).trim(),
      routing_order: Number(textOf(form, `order-${row.id}`)) || 1,
    }));
    send.mutate(signers);
  }

  return (
    <form className="form form--wide" onSubmit={handleSubmit} noValidate>
      <FormError message={send.isError ? errorMessage(send.error) : null} />
      {rows.map((row, index) => (
        <fieldset key={row.id} className="columns">
          <legend>Signatário {index + 1}</legend>
          <TextField label="Nome" name={`name-${row.id}`} required />
          <TextField label="E-mail" name={`email-${row.id}`} type="email" required />
          <TextField
            label="Ordem"
            name={`order-${row.id}`}
            type="number"
            min={1}
            defaultValue={index + 1}
            required
          />
          {rows.length > 1 && (
            <Button
              variant="link"
              onClick={() => {
                setRows((current) => current.filter((item) => item.id !== row.id));
              }}
            >
              Remover signatário {index + 1}
            </Button>
          )}
        </fieldset>
      ))}
      <Button
        variant="secondary"
        onClick={() => {
          setRows((current) => [...current, { id: Math.max(0, ...current.map((r) => r.id)) + 1 }]);
        }}
      >
        Adicionar signatário
      </Button>
      <Button type="submit" busy={send.isPending}>
        Enviar para assinatura
      </Button>
    </form>
  );
}

export function SignatureTab() {
  const contract = useContractContext();
  const key = useOrgKey();
  const { can } = useSession();
  const envelopes = useQuery({
    queryKey: key("contracts", "envelopes", contract.id),
    queryFn: () =>
      unwrap(api.GET("/api/v1/envelopes", { params: { query: { contract_id: contract.id } } })),
    // Enquanto o worker envia ou cancela, a situação muda sem ação do usuário.
    refetchInterval: (query) =>
      query.state.data?.some((envelope) => IN_FLIGHT.has(envelope.state)) ? 2000 : false,
  });

  return (
    <>
      {contract.drafting_status === "approved" &&
        contract.approved_fingerprint &&
        can("signatures.send") && (
          <Section title="Enviar para assinatura">
            <p className="muted">
              Será enviado exatamente o documento da revisão aprovada; o hash do arquivo enviado
              fica registrado.
            </p>
            <SendForm contractId={contract.id} fingerprint={contract.approved_fingerprint} />
          </Section>
        )}
      {contract.drafting_status === "draft" && (
        <p>O envio para assinatura exige uma revisão aprovada.</p>
      )}
      <Section title="Envelopes">
        <QueryView query={envelopes}>
          {(items) =>
            items.length === 0 ? (
              <EmptyState title="Nenhum envelope enviado." />
            ) : (
              items.map((envelope) => <EnvelopeCard key={envelope.id} envelope={envelope} />)
            )
          }
        </QueryView>
      </Section>
    </>
  );
}
