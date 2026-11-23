import { useMutation, useQuery } from "@tanstack/react-query";
import { Link } from "react-router";

import { api, unwrap } from "../../api/client";
import { errorMessage } from "../../api/errors";
import { useInvalidate, useOrgKey } from "../../app/queries";
import { useSession } from "../../auth/session";
import { FormError } from "../../components/forms";
import { useNotify } from "../../components/notifications";
import { EmptyState, QueryView } from "../../components/states";
import { Button, Section } from "../../components/ui";
import { useIdempotencyKey } from "../../lib/idempotency";
import { RoundView } from "../approvals/RoundView";
import { useContractContext } from "./ContractLayout";

export function ApprovalTab() {
  const contract = useContractContext();
  const key = useOrgKey();
  const { can } = useSession();
  const notify = useNotify();
  const invalidate = useInvalidate();
  const idempotency = useIdempotencyKey();
  const rounds = useQuery({
    queryKey: key("contracts", "rounds", contract.id),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/approval-rounds", { params: { query: { contract_id: contract.id } } }),
      ),
  });
  const current = contract.current_revision;
  const submit = useMutation({
    mutationFn: (fingerprint: string) =>
      unwrap(
        api.POST("/api/v1/approval-rounds/submit", {
          params: { header: { "Idempotency-Key": idempotency.key } },
          body: { contract_id: contract.id, expected_fingerprint: fingerprint },
        }),
      ),
    onSuccess: async () => {
      idempotency.settle();
      notify.success("Revisão enviada para aprovação.");
      await invalidate("contracts");
    },
    onError: (error) => {
      idempotency.settle(error);
    },
  });

  const canSubmit =
    can("contracts.submit") && contract.drafting_status === "draft" && current !== null;

  return (
    <>
      <Section title="Enviar para aprovação">
        {current === null ? (
          <p>Publique uma revisão do rascunho antes de enviar para aprovação.</p>
        ) : (
          <p>
            A aprovação fica vinculada à revisão {current.number} e à sua impressão digital (
            <code className="secret">{current.fingerprint.slice(0, 16)}…</code>). Qualquer nova
            revisão ou alteração de dados invalida a aprovação.
          </p>
        )}
        <FormError message={submit.isError ? errorMessage(submit.error) : null} />
        {canSubmit && (
          <Button
            busy={submit.isPending}
            onClick={() => {
              submit.mutate(current.fingerprint);
            }}
          >
            Enviar revisão {current.number} para aprovação
          </Button>
        )}
      </Section>
      <Section title="Rodadas de aprovação">
        <QueryView query={rounds}>
          {(items) =>
            items.length === 0 ? (
              <EmptyState title="Nenhuma rodada de aprovação." />
            ) : (
              <ul className="rounds">
                {items.map((round) => (
                  <li key={round.id}>
                    <RoundView round={round} />
                    {round.status === "open" && can("approvals.vote") && (
                      <Link to={`/aprovacoes/${round.id}`}>Abrir para votar</Link>
                    )}
                  </li>
                ))}
              </ul>
            )
          }
        </QueryView>
      </Section>
    </>
  );
}
