import { useMutation, useQuery } from "@tanstack/react-query";

import { api, unwrap } from "../../api/client";
import { errorMessage } from "../../api/errors";
import { useInvalidate, useOrgKey } from "../../app/queries";
import { FormError } from "../../components/forms";
import { useNotify } from "../../components/notifications";
import { Restricted } from "../../components/Restricted";
import { EmptyState, QueryView } from "../../components/states";
import { Badge, Button, PageHeader, Section, Table } from "../../components/ui";
import { formatDateTime } from "../../lib/format";
import { FAILURE_STATUS } from "../../lib/labels";

function Operations() {
  const key = useOrgKey();
  const notify = useNotify();
  const invalidate = useInvalidate();
  const failures = useQuery({
    queryKey: key("operations", "failures"),
    queryFn: () => unwrap(api.GET("/api/v1/operations/failures")),
  });
  const requeue = useMutation({
    mutationFn: (id: string) =>
      unwrap(
        api.POST("/api/v1/operations/failures/{failure_id}/requeue", {
          params: { path: { failure_id: id } },
        }),
      ),
    onSuccess: async () => {
      notify.success("Evento reenviado ao consumidor.");
      await invalidate("operations");
    },
  });
  const dead = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/operations/dead-events/requeue")),
    onSuccess: async (result) => {
      notify.success(`${result.requeued} eventos esgotados voltaram para a fila.`);
      await invalidate("operations");
    },
  });

  return (
    <>
      <Section
        title="Falhas de processamento"
        actions={
          <Button
            variant="secondary"
            busy={dead.isPending}
            onClick={() => {
              dead.mutate();
            }}
          >
            Reenfileirar eventos esgotados
          </Button>
        }
      >
        <FormError
          message={(requeue.error ?? dead.error) ? errorMessage(requeue.error ?? dead.error) : null}
        />
        <QueryView query={failures}>
          {(items) =>
            items.length === 0 ? (
              <EmptyState title="Nenhuma falha registrada." />
            ) : (
              <Table caption="Falhas de consumidores">
                <thead>
                  <tr>
                    <th scope="col">Evento</th>
                    <th scope="col">Consumidor</th>
                    <th scope="col">Tentativas</th>
                    <th scope="col">Último erro</th>
                    <th scope="col">Atualizado em</th>
                    <th scope="col">Situação</th>
                    <th scope="col">
                      <span className="visually-hidden">Ações</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((failure) => (
                    <tr key={failure.id}>
                      <td>{failure.topic}</td>
                      <td>
                        <code>{failure.consumer}</code>
                      </td>
                      <td>{failure.attempts}</td>
                      <td className="truncate">{failure.last_error}</td>
                      <td>{formatDateTime(failure.updated_at)}</td>
                      <td>
                        <Badge tone={failure.status === "failed" ? "danger" : "neutral"}>
                          {FAILURE_STATUS[failure.status]}
                        </Badge>
                      </td>
                      <td>
                        {failure.status === "failed" && (
                          <Button
                            variant="link"
                            busy={requeue.isPending && requeue.variables === failure.id}
                            onClick={() => {
                              requeue.mutate(failure.id);
                            }}
                          >
                            Reprocessar
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
      </Section>
    </>
  );
}

export function OperationsPage() {
  return (
    <Restricted permission="operations.reprocess">
      <PageHeader
        title="Operação"
        description="Reprocessamento auditado; o evento original é reenviado e a deduplicação dos consumidores continua valendo."
      />
      <Operations />
    </Restricted>
  );
}
