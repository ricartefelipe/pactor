import { useInfiniteQuery } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router";

import { api, unwrap } from "../../api/client";
import { cursorOf, useOrgKey } from "../../app/queries";
import { useSession } from "../../auth/session";
import { SelectField, TextField, textOf } from "../../components/forms";
import { LoadMore } from "../../components/LoadMore";
import { Restricted } from "../../components/Restricted";
import { EmptyState, QueryView } from "../../components/states";
import { Badge, Button, PageHeader, Table } from "../../components/ui";
import { enumValue, options, PARTY_KIND } from "../../lib/labels";

function CounterpartyList() {
  const key = useOrgKey();
  const { can } = useSession();
  const [params, setParams] = useSearchParams();
  const q = params.get("q") ?? "";
  const kind = enumValue(PARTY_KIND, params.get("tipo") ?? undefined);
  const list = useInfiniteQuery({
    queryKey: key("counterparties", { q, kind: kind ?? null }),
    queryFn: ({ pageParam }) =>
      unwrap(
        api.GET("/api/v1/counterparties", {
          params: { query: { ...(q ? { q } : {}), ...(kind ? { kind } : {}), cursor: pageParam } },
        }),
      ),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (page) => cursorOf(page.next),
  });

  return (
    <>
      <PageHeader
        title="Contrapartes"
        actions={
          can("counterparties.write") && (
            <Link className="button button--primary" to="/contrapartes/nova">
              Nova contraparte
            </Link>
          )
        }
      />
      <form
        className="filters"
        role="search"
        onSubmit={(event) => {
          event.preventDefault();
          const form = event.currentTarget;
          const next = new URLSearchParams();
          const text = textOf(form, "q").trim();
          const chosen = textOf(form, "tipo");
          if (text) next.set("q", text);
          if (chosen) next.set("tipo", chosen);
          setParams(next);
        }}
      >
        <TextField label="Nome ou documento" name="q" defaultValue={q} />
        <SelectField
          label="Tipo"
          name="tipo"
          defaultValue={kind ?? ""}
          placeholder="Todos"
          options={options(PARTY_KIND)}
        />
        <Button type="submit" variant="secondary">
          Filtrar
        </Button>
      </form>
      <QueryView query={list}>
        {(data) => {
          const rows = data.pages.flatMap((page) => page.results);
          if (rows.length === 0) return <EmptyState title="Nenhuma contraparte encontrada." />;
          return (
            <>
              <Table caption="Contrapartes">
                <thead>
                  <tr>
                    <th scope="col">Nome</th>
                    <th scope="col">Tipo</th>
                    <th scope="col">Documento</th>
                    <th scope="col">Situação</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => (
                    <tr key={row.id}>
                      <td>
                        <Link to={`/contrapartes/${row.id}`}>{row.name}</Link>
                      </td>
                      <td>{PARTY_KIND[row.kind]}</td>
                      <td>{row.document}</td>
                      <td>
                        <Badge tone={row.is_active ? "success" : "neutral"}>
                          {row.is_active ? "Ativa" : "Inativa"}
                        </Badge>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </Table>
              <LoadMore query={list} />
            </>
          );
        }}
      </QueryView>
    </>
  );
}

export function CounterpartiesPage() {
  return (
    <Restricted permission="counterparties.read">
      <CounterpartyList />
    </Restricted>
  );
}
