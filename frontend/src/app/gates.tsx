import { useEffect, useRef } from "react";
import { Navigate, Outlet, useLocation } from "react-router";

import { SessionProvider, useAuthStep, useMeQuery, useSession } from "../auth/session";
import { ErrorState, Loading } from "../components/states";
import { ChooseOrganizationPage } from "../features/account/ChooseOrganizationPage";
import { Layout } from "./Layout";

/** Exige sessão autenticada e carrega o usuário; sem sessão, vai para o login. */
export function AuthGate() {
  const step = useAuthStep();
  const location = useLocation();
  const authenticated = step.data === "authenticated";
  const me = useMeQuery(authenticated);

  if (step.isPending) return <Loading label="Verificando sessão…" />;
  if (step.isError) {
    return (
      <ErrorState
        error={step.error}
        onRetry={() => {
          void step.refetch();
        }}
      />
    );
  }
  if (!authenticated) {
    const next = `${location.pathname}${location.search}`;
    return <Navigate to={`/entrar?proxima=${encodeURIComponent(next)}`} replace />;
  }
  if (me.isPending) return <Loading label="Carregando seus dados…" />;
  if (me.isError) {
    return (
      <ErrorState
        error={me.error}
        onRetry={() => {
          void me.refetch();
        }}
      />
    );
  }
  return (
    <SessionProvider me={me.data}>
      <Outlet />
    </SessionProvider>
  );
}

/** Páginas de trabalho exigem organização ativa; com uma única, ela é escolhida sozinha. */
export function OrganizationGate() {
  const { me, active, switchOrganization } = useSession();
  const attempted = useRef(false);
  const only = me.organizations.length === 1 ? me.organizations[0] : undefined;

  useEffect(() => {
    if (active === null && only !== undefined && !attempted.current) {
      attempted.current = true;
      void switchOrganization(only.id);
    }
  }, [active, only, switchOrganization]);

  if (active === null) {
    return only === undefined ? (
      <ChooseOrganizationPage />
    ) : (
      <Loading label="Abrindo organização…" />
    );
  }
  return <Layout />;
}
