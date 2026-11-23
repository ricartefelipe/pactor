/**
 * Chaves de consulta sempre prefixadas pela organização ativa. A troca de
 * organização também limpa todo o cache; o prefixo é uma segunda barreira
 * contra exibir dados de outra organização.
 */
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback } from "react";

import { api, unwrap } from "../api/client";
import { useActiveOrganization } from "../auth/session";

export type QueryKeyPart = string | number | boolean | null | Readonly<Record<string, unknown>>;

export function useOrgKey(): (...parts: QueryKeyPart[]) => readonly QueryKeyPart[] {
  const { organization_id: organizationId } = useActiveOrganization();
  return useCallback((...parts) => ["org", organizationId, ...parts], [organizationId]);
}

/** Invalida consultas da organização ativa pelo prefixo informado. */
export function useInvalidate(): (...parts: QueryKeyPart[]) => Promise<void> {
  const queryClient = useQueryClient();
  const key = useOrgKey();
  return useCallback(
    (...parts) => queryClient.invalidateQueries({ queryKey: key(...parts) }),
    [queryClient, key],
  );
}

const REFERENCE_STALE_MS = 5 * 60_000;

export function useDirectory() {
  const key = useOrgKey();
  return useQuery({
    queryKey: key("directory"),
    queryFn: () => unwrap(api.GET("/api/v1/members/directory")),
    staleTime: REFERENCE_STALE_MS,
  });
}

export function useDepartments() {
  const key = useOrgKey();
  return useQuery({
    queryKey: key("departments"),
    queryFn: () => unwrap(api.GET("/api/v1/departments")),
    staleTime: REFERENCE_STALE_MS,
  });
}

export function useTeams() {
  const key = useOrgKey();
  return useQuery({
    queryKey: key("teams"),
    queryFn: () => unwrap(api.GET("/api/v1/teams")),
    staleTime: REFERENCE_STALE_MS,
  });
}

export function useActiveCounterparties() {
  const key = useOrgKey();
  return useQuery({
    queryKey: key("counterparties", "options"),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/counterparties", {
          params: { query: { is_active: true, page_size: 100 } },
        }),
      ),
    staleTime: REFERENCE_STALE_MS,
  });
}

/** Nome de um membro a partir do diretório (ids de usuário vêm crus da API). */
export function useMemberName(): (userId: string | null | undefined) => string {
  const directory = useDirectory();
  return useCallback(
    (userId) => {
      if (!userId) return "—";
      const entry = directory.data?.find((member) => member.user_id === userId);
      return entry ? entry.full_name || entry.email : "Membro removido";
    },
    [directory.data],
  );
}

/** Cursor da próxima página a partir do link `next` da paginação por cursor. */
export function cursorOf(next: string | null | undefined): string | undefined {
  if (!next) return undefined;
  return new URL(next, window.location.origin).searchParams.get("cursor") ?? undefined;
}
