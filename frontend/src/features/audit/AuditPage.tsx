import { useInfiniteQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router";

import { api, unwrap } from "../../api/client";
import { cursorOf, useOrgKey } from "../../app/queries";
import { TextField, textOf } from "../../components/forms";
import { LoadMore } from "../../components/LoadMore";
import { Restricted } from "../../components/Restricted";
import { EmptyState, QueryView } from "../../components/states";
import { Button, PageHeader, Table } from "../../components/ui";
import { formatDateTime } from "../../lib/format";

function changesText(changes: unknown): string {
  if (changes === null || changes === undefined) return "—";
  const text = JSON.stringify(changes);
  return text === "{}" ? "—" : text;
}

function Audit() {
  const key = useOrgKey();
  const [params, setParams] = useSearchParams();
  const action = params.get("acao") ?? "";
  const resource = params.get("recurso") ?? "";
  const events = useInfiniteQuery({
    queryKey: key("audit", { action, resource }),
    queryFn: ({ pageParam }) =>
      unwrap(
        api.GET("/api/v1/audit-events", {
          params: {
            query: {
              ...(action ? { action } : {}),
              ...(resource ? { resource_type: resource } : {}),
              cursor: pageParam,
            },
          },
        }),
      ),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (page) => cursorOf(page.next),
  });

  return (
    <>
      <PageHeader
        title="Auditoria"
        description="Registro somente de inclusão, com dados sensíveis mascarados."
      />
      <form
        className="filters"
        role="search"
        onSubmit={(event) => {
          event.preventDefault();
          const form = event.currentTarget;
          const next = new URLSearchParams();
          const a = textOf(form, "acao").trim();
          const r = textOf(form, "recurso").trim();
          if (a) next.set("acao", a);
          if (r) next.set("recurso", r);
          setParams(next);
        }}
      >
        <TextField label="Ação" name="acao" defaultValue={action} hint="Ex.: contract.created" />
        <TextField
          label="Tipo de recurso"
          name="recurso"
          defaultValue={resource}
          hint="Ex.: contract"
        />
        <Button type="submit" variant="secondary">
          Filtrar
        </Button>
      </form>
      <QueryView query={events}>
        {(data) => {
          const rows = data.pages.flatMap((page) => page.results);
          if (rows.length === 0) return <EmptyState title="Nenhum evento." />;
          return (
            <>
              <Table caption="Eventos de auditoria">
                <thead>
                  <tr>
                    <th scope="col">Quando</th>
                    <th scope="col">Quem</th>
                    <th scope="col">Ação</th>
                    <th scope="col">Recurso</th>
                    <th scope="col">Alterações</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((event) => (
                    <tr key={event.id}>
                      <td>{formatDateTime(event.occurred_at)}</td>
                      <td>{event.actor_label || "Sistema"}</td>
                      <td>
                        <code>{event.action}</code>
                      </td>
                      <td>
                        {event.resource_type}{" "}
                        <code className="secret">{event.resource_id.slice(0, 8)}</code>
                      </td>
                      <td className="truncate">{changesText(event.changes)}</td>
                    </tr>
                  ))}
                </tbody>
              </Table>
              <LoadMore query={events} />
            </>
          );
        }}
      </QueryView>
    </>
  );
}

export function AuditPage() {
  return (
    <Restricted permission="audit.read">
      <Audit />
    </Restricted>
  );
}
