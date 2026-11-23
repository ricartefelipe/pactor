import { useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { createContext, use, useCallback, useMemo, type ReactNode } from "react";

import * as auth from "../api/auth";
import { api, unwrap, type Schemas } from "../api/client";

export type Me = Schemas["Me"];
export type ActiveOrganization = Schemas["ActiveOrganization"];
export type Permission = Schemas["PermissionEnum"];

export const AUTH_KEY = ["auth", "session"] as const;
export const ME_KEY = ["me"] as const;

/**
 * Descarta do cache tudo o que não identifica a sessão (organização, contratos,
 * listas...). Remove em vez de limpar o cliente inteiro: as consultas de sessão
 * continuam observadas e recebem os novos valores sem recarregar a página.
 */
export function discardCachedData(
  client: QueryClient,
  keep: readonly string[] = ["auth", "me"],
): void {
  client.removeQueries({ predicate: (query) => !keep.includes(String(query.queryKey[0])) });
}

interface Session {
  me: Me;
  active: ActiveOrganization | null;
  /** Permissão concedida agora (já considerando MFA). */
  can: (permission: Permission) => boolean;
  /** Permissão que seria concedida após concluir o segundo fator. */
  needsMfa: (permission: Permission) => boolean;
  switchOrganization: (organizationId: string) => Promise<void>;
  signOut: () => Promise<void>;
}

const SessionContext = createContext<Session | null>(null);

export function useAuthStep() {
  return useQuery({ queryKey: AUTH_KEY, queryFn: auth.session, staleTime: 60_000 });
}

export function useMeQuery(enabled: boolean) {
  return useQuery({
    queryKey: ME_KEY,
    queryFn: () => unwrap(api.GET("/api/v1/me")),
    enabled,
    staleTime: 60_000,
  });
}

export function SessionProvider({ me, children }: { me: Me; children: ReactNode }) {
  const queryClient = useQueryClient();

  const switchOrganization = useCallback(
    async (organizationId: string) => {
      const updated = await unwrap(
        api.PUT("/api/v1/session/organization", { body: { organization_id: organizationId } }),
      );
      // Nada de uma organização pode aparecer em outra: descarta todo o cache de dados.
      discardCachedData(queryClient);
      queryClient.setQueryData(ME_KEY, updated);
    },
    [queryClient],
  );

  const signOut = useCallback(async () => {
    await auth.logout();
    discardCachedData(queryClient, ["auth"]);
    queryClient.setQueryData(AUTH_KEY, "anonymous");
  }, [queryClient]);

  const value = useMemo<Session>(() => {
    const active = me.active_organization;
    const granted = new Set(active?.permissions ?? []);
    const pendingMfa = new Set(active?.mfa_required_for ?? []);
    return {
      me,
      active,
      can: (permission) => granted.has(permission),
      needsMfa: (permission) => pendingMfa.has(permission),
      switchOrganization,
      signOut,
    };
  }, [me, switchOrganization, signOut]);

  return <SessionContext value={value}>{children}</SessionContext>;
}

export function useSession(): Session {
  const session = use(SessionContext);
  if (session === null) throw new Error("useSession fora de SessionProvider");
  return session;
}

/** Organização ativa; as páginas da área logada só existem com uma. */
export function useActiveOrganization(): ActiveOrganization {
  const { active } = useSession();
  if (active === null) throw new Error("Página exige organização ativa");
  return active;
}
