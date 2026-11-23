import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router";

import { api, unwrap } from "../../api/client";
import { useOrgKey } from "../../app/queries";
import { TextField } from "../../components/forms";
import { Restricted } from "../../components/Restricted";
import { EmptyState, QueryView } from "../../components/states";
import { Badge, Button, PageHeader } from "../../components/ui";
import {
  CONTRACT_TYPE,
  DRAFTING_STATUS,
  LIFECYCLE_STATUS,
  enumValue,
  labelOf,
} from "../../lib/labels";
import { snippetParts } from "../../lib/snippet";

const PAGE_SIZE = 20;
const FACETS = [
  { field: "contract_type", title: "Tipo", labels: CONTRACT_TYPE },
  { field: "drafting_status", title: "Elaboração", labels: DRAFTING_STATUS },
  { field: "lifecycle_status", title: "Vigência", labels: LIFECYCLE_STATUS },
] as const;

function useDebounced(value: string, delayMs: number): string {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = window.setTimeout(() => {
      setDebounced(value);
    }, delayMs);
    return () => {
      window.clearTimeout(timer);
    };
  }, [value, delayMs]);
  return debounced;
}

function Suggestions({ text }: { text: string }) {
  const key = useOrgKey();
  const q = useDebounced(text.trim(), 250);
  const suggestions = useQuery({
    queryKey: key("search", "suggest", q),
    queryFn: () => unwrap(api.GET("/api/v1/search/suggest", { params: { query: { q } } })),
    enabled: q.length >= 2,
  });
  const items = suggestions.data ?? [];
  return (
    <div aria-live="polite">
      {items.length > 0 && (
        <ul className="suggestions" aria-label="Sugestões">
          {items.map((item) => (
            <li key={item.id}>
              <Link to={`/contratos/${item.id}`}>
                {item.reference} · {item.title}
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Search() {
  const key = useOrgKey();
  const [params, setParams] = useSearchParams();
  const q = params.get("q") ?? "";
  const page = Math.max(1, Number(params.get("pagina") ?? "1") || 1);
  const filters = {
    contract_type: enumValue(CONTRACT_TYPE, params.get("contract_type") ?? undefined),
    drafting_status: enumValue(DRAFTING_STATUS, params.get("drafting_status") ?? undefined),
    lifecycle_status: enumValue(LIFECYCLE_STATUS, params.get("lifecycle_status") ?? undefined),
  };
  const [text, setText] = useState(q);
  const search = useQuery({
    queryKey: key("search", { q, page, ...filters }),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/search", {
          params: {
            query: {
              ...(q ? { q } : {}),
              ...(filters.contract_type ? { contract_type: filters.contract_type } : {}),
              ...(filters.drafting_status ? { drafting_status: filters.drafting_status } : {}),
              ...(filters.lifecycle_status ? { lifecycle_status: filters.lifecycle_status } : {}),
              page,
              size: PAGE_SIZE,
            },
          },
        }),
      ),
  });

  function update(changes: Record<string, string | null>) {
    const next = new URLSearchParams(params);
    for (const [name, value] of Object.entries(changes)) {
      if (value === null || value === "") next.delete(name);
      else next.set(name, value);
    }
    if (!("pagina" in changes)) next.delete("pagina");
    setParams(next);
  }

  return (
    <>
      <PageHeader
        title="Pesquisa"
        description="Busca no texto das revisões publicadas e nos anexos liberados, somente entre os contratos que você pode ver."
      />
      <form
        className="filters"
        role="search"
        onSubmit={(event) => {
          event.preventDefault();
          update({ q: text.trim() });
        }}
      >
        <TextField
          label="Termos ou referência"
          name="q"
          value={text}
          onChange={(event) => {
            setText(event.target.value);
          }}
          autoComplete="off"
        />
        <Button type="submit">Pesquisar</Button>
      </form>
      <Suggestions text={text} />
      <QueryView query={search}>
        {(data) => {
          const pages = Math.max(1, Math.ceil(data.total / data.page_size));
          return (
            <div className="search">
              <aside className="facets" aria-label="Refinar resultados">
                {FACETS.map((facet) => {
                  const counts = Object.entries(data.facets[facet.field]);
                  if (counts.length === 0) return null;
                  const selected = filters[facet.field];
                  return (
                    <fieldset key={facet.field}>
                      <legend>{facet.title}</legend>
                      <ul>
                        {counts.map(([value, count]) => (
                          <li key={value}>
                            <Button
                              variant="link"
                              aria-pressed={selected === value}
                              onClick={() => {
                                update({ [facet.field]: selected === value ? null : value });
                              }}
                            >
                              {labelOf(facet.labels, value)} ({count})
                            </Button>
                          </li>
                        ))}
                      </ul>
                    </fieldset>
                  );
                })}
              </aside>
              <div>
                <p role="status">
                  {data.total} {data.total === 1 ? "contrato encontrado" : "contratos encontrados"}
                </p>
                {data.results.length === 0 ? (
                  <EmptyState title="Nenhum resultado." />
                ) : (
                  <ol className="results" start={(data.page - 1) * data.page_size + 1}>
                    {data.results.map((result) => (
                      <li key={result.id}>
                        <h2>
                          <Link to={`/contratos/${result.id}`}>
                            {result.reference} · {result.title}
                          </Link>
                        </h2>
                        <p className="muted">
                          {result.counterparty_name} · {CONTRACT_TYPE[result.contract_type]} ·{" "}
                          <Badge>{DRAFTING_STATUS[result.drafting_status]}</Badge>{" "}
                          <Badge>{LIFECYCLE_STATUS[result.lifecycle_status]}</Badge>
                        </p>
                        {result.snippet && (
                          <p>
                            {snippetParts(result.snippet).map((part, index) =>
                              part.match ? (
                                <mark key={index}>{part.text}</mark>
                              ) : (
                                <span key={index}>{part.text}</span>
                              ),
                            )}
                          </p>
                        )}
                      </li>
                    ))}
                  </ol>
                )}
                {pages > 1 && (
                  <nav aria-label="Páginas de resultados" className="pagination">
                    <Button
                      variant="secondary"
                      disabled={data.page <= 1}
                      onClick={() => {
                        update({ pagina: String(data.page - 1) });
                      }}
                    >
                      Anterior
                    </Button>
                    <span>
                      Página {data.page} de {pages}
                    </span>
                    <Button
                      variant="secondary"
                      disabled={data.page >= pages}
                      onClick={() => {
                        update({ pagina: String(data.page + 1) });
                      }}
                    >
                      Próxima
                    </Button>
                  </nav>
                )}
              </div>
            </div>
          );
        }}
      </QueryView>
    </>
  );
}

export function SearchPage() {
  return (
    <Restricted permission="contracts.read">
      <Search />
    </Restricted>
  );
}
