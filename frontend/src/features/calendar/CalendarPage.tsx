import { useQuery } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router";

import { api, unwrap } from "../../api/client";
import { useOrgKey } from "../../app/queries";
import { TextField, textOf } from "../../components/forms";
import { Restricted } from "../../components/Restricted";
import { EmptyState, QueryView } from "../../components/states";
import { Badge, Button, PageHeader, Table } from "../../components/ui";
import { formatDate } from "../../lib/format";
import {
  DEADLINE_KIND,
  DEADLINE_STATUS,
  OBLIGATION_CATEGORY,
  OCCURRENCE_STATUS,
  labelOf,
} from "../../lib/labels";

function Calendar() {
  const key = useOrgKey();
  const [params, setParams] = useSearchParams();
  const start = params.get("inicio") ?? undefined;
  const end = params.get("fim") ?? undefined;
  const calendar = useQuery({
    queryKey: key("calendar", { start: start ?? null, end: end ?? null }),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/calendar", {
          params: { query: { ...(start ? { start } : {}), ...(end ? { end } : {}) } },
        }),
      ),
  });

  return (
    <>
      <PageHeader
        title="Calendário"
        description="Prazos e ocorrências de obrigações dos contratos que você pode ver."
      />
      <form
        className="filters"
        onSubmit={(event) => {
          event.preventDefault();
          const form = event.currentTarget;
          const next = new URLSearchParams();
          const from = textOf(form, "inicio");
          const to = textOf(form, "fim");
          if (from) next.set("inicio", from);
          if (to) next.set("fim", to);
          setParams(next);
        }}
      >
        <TextField label="De" name="inicio" type="date" defaultValue={start ?? ""} />
        <TextField label="Até" name="fim" type="date" defaultValue={end ?? ""} />
        <Button type="submit" variant="secondary">
          Mostrar período
        </Button>
      </form>
      <QueryView query={calendar}>
        {(data) => (
          <>
            <p className="muted">
              De {formatDate(data.start)} a {formatDate(data.end)} (hoje: {formatDate(data.today)}).
            </p>
            {data.events.length === 0 ? (
              <EmptyState title="Nada programado no período." />
            ) : (
              <Table caption="Eventos do calendário">
                <thead>
                  <tr>
                    <th scope="col">Data</th>
                    <th scope="col">Evento</th>
                    <th scope="col">Tipo</th>
                    <th scope="col">Contrato</th>
                    <th scope="col">Situação</th>
                  </tr>
                </thead>
                <tbody>
                  {data.events.map((event) => (
                    <tr key={`${event.type}-${event.id}`}>
                      <td>{formatDate(event.date)}</td>
                      <td>{event.title}</td>
                      <td>
                        {event.type === "deadline"
                          ? `Prazo: ${labelOf(DEADLINE_KIND, event.kind)}`
                          : `Obrigação: ${labelOf(OBLIGATION_CATEGORY, event.kind)}`}
                      </td>
                      <td>
                        <Link to={`/contratos/${event.contract_id}`}>
                          {event.contract_reference}
                        </Link>
                      </td>
                      <td>
                        {event.overdue ? (
                          <Badge tone="danger">Em atraso</Badge>
                        ) : (
                          <Badge>
                            {event.type === "deadline"
                              ? labelOf(DEADLINE_STATUS, event.status)
                              : labelOf(OCCURRENCE_STATUS, event.status)}
                          </Badge>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </Table>
            )}
          </>
        )}
      </QueryView>
    </>
  );
}

export function CalendarPage() {
  return (
    <Restricted permission="contracts.read">
      <Calendar />
    </Restricted>
  );
}
