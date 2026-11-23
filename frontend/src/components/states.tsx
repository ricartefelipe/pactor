import type { UseQueryResult } from "@tanstack/react-query";
import type { ReactNode } from "react";

import { ApiError, errorMessage } from "../api/errors";
import { Button } from "./ui";

export function Loading({ label = "Carregando…" }: { label?: string }) {
  return (
    <p className="state state--loading" role="status" aria-live="polite">
      {label}
    </p>
  );
}

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="state state--empty">
      <p className="state__title">{title}</p>
      {children}
    </div>
  );
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const requestId = error instanceof ApiError ? error.requestId : null;
  return (
    <div className="state state--error" role="alert">
      <p className="state__title">{errorMessage(error)}</p>
      {requestId && <p className="state__detail">Código para suporte: {requestId}</p>}
      {onRetry && (
        <Button variant="secondary" onClick={onRetry}>
          Tentar novamente
        </Button>
      )}
    </div>
  );
}

/** Renderiza carregamento, erro ou o conteúdo de uma consulta. */
export function QueryView<T>({
  query,
  loadingLabel,
  children,
}: {
  query: UseQueryResult<T>;
  loadingLabel?: string;
  children: (data: T) => ReactNode;
}) {
  if (query.isPending) return <Loading {...(loadingLabel ? { label: loadingLabel } : {})} />;
  if (query.isError) {
    return (
      <ErrorState
        error={query.error}
        onRetry={() => {
          void query.refetch();
        }}
      />
    );
  }
  return <>{children(query.data)}</>;
}

/**
 * Renderiza o conteúdo só quando todas as consultas de referência carregaram.
 * Formulários com valores iniciais em listas carregadas da API (responsável,
 * departamento...) dependem disso: um `<select>` montado antes das opções
 * selecionaria a primeira opção em silêncio.
 */
export function WhenReady({
  queries,
  children,
}: {
  queries: readonly UseQueryResult[];
  children: () => ReactNode;
}) {
  const failed = queries.find((query) => query.isError);
  if (failed) {
    return (
      <ErrorState
        error={failed.error}
        onRetry={() => {
          for (const query of queries) void query.refetch();
        }}
      />
    );
  }
  if (queries.some((query) => query.isPending)) return <Loading />;
  return <>{children()}</>;
}
