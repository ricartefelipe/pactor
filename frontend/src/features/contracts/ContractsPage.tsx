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
import { formatDate, formatMoney } from "../../lib/format";
import {
  CONFIDENTIALITY,
  CONTRACT_TYPE,
  DRAFTING_STATUS,
  enumValue,
  options,
} from "../../lib/labels";
import { draftingTone } from "./tones";

function ContractList() {
  const key = useOrgKey();
  const { can } = useSession();
  const [params, setParams] = useSearchParams();
  const q = params.get("q") ?? "";
  const status = enumValue(DRAFTING_STATUS, params.get("situacao") ?? undefined);
  const type = enumValue(CONTRACT_TYPE, params.get("tipo") ?? undefined);
  const list = useInfiniteQuery({
    queryKey: key("contracts", "list", { q, status: status ?? null, type: type ?? null }),
    queryFn: ({ pageParam }) =>
      unwrap(
        api.GET("/api/v1/contracts", {
          params: {
            query: {
              ...(q ? { q } : {}),
              ...(status ? { drafting_status: status } : {}),
              ...(type ? { contract_type: type } : {}),
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
        title="Contratos"
        actions={
          can("contracts.create") && (
            <Link className="button button--primary" to="/contratos/novo">
              Novo contrato
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
          for (const [field, param] of [
            ["q", "q"],
            ["situacao", "situacao"],
            ["tipo", "tipo"],
          ] as const) {
            const value = textOf(form, field).trim();
            if (value) next.set(param, value);
          }
          setParams(next);
        }}
      >
        <TextField label="Título ou referência" name="q" defaultValue={q} />
        <SelectField
          label="Situação"
          name="situacao"
          defaultValue={status ?? ""}
          placeholder="Todas"
          options={options(DRAFTING_STATUS)}
        />
        <SelectField
          label="Tipo"
          name="tipo"
          defaultValue={type ?? ""}
          placeholder="Todos"
          options={options(CONTRACT_TYPE)}
        />
        <Button type="submit" variant="secondary">
          Filtrar
        </Button>
      </form>
      <QueryView query={list}>
        {(data) => {
          const rows = data.pages.flatMap((page) => page.results);
          if (rows.length === 0) return <EmptyState title="Nenhum contrato encontrado." />;
          return (
            <>
              <Table caption="Contratos">
                <thead>
                  <tr>
                    <th scope="col">Referência</th>
                    <th scope="col">Título</th>
                    <th scope="col">Contraparte</th>
                    <th scope="col">Valor</th>
                    <th scope="col">Período previsto</th>
                    <th scope="col">Situação</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => (
                    <tr key={row.id}>
                      <td>{row.reference}</td>
                      <td>
                        <Link to={`/contratos/${row.id}`}>{row.title}</Link>
                        {row.confidentiality !== "internal" && (
                          <>
                            {" "}
                            <Badge tone="warning">{CONFIDENTIALITY[row.confidentiality]}</Badge>
                          </>
                        )}
                      </td>
                      <td>{row.counterparty_name}</td>
                      <td>{formatMoney(row.total_value)}</td>
                      <td>
                        {formatDate(row.planned_start)} a {formatDate(row.planned_end)}
                      </td>
                      <td>
                        <Badge tone={draftingTone(row.drafting_status)}>
                          {DRAFTING_STATUS[row.drafting_status]}
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

export function ContractsPage() {
  return (
    <Restricted permission="contracts.read">
      <ContractList />
    </Restricted>
  );
}
