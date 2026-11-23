import type { ReactNode } from "react";
import { Link, useLocation } from "react-router";

import { useSession, type Permission } from "../auth/session";
import { EmptyState } from "./states";

/** Conteúdo que exige uma permissão; explica o motivo quando ela falta. */
export function Restricted({
  permission,
  children,
}: {
  permission: Permission;
  children: ReactNode;
}) {
  const { can, needsMfa } = useSession();
  const location = useLocation();
  if (can(permission)) return <>{children}</>;
  if (needsMfa(permission)) {
    return (
      <EmptyState title="Esta área exige verificação em duas etapas.">
        <p>
          Confirme um código do seu aplicativo autenticador em{" "}
          <Link to={`/conta/seguranca?proxima=${encodeURIComponent(location.pathname)}`}>
            Segurança
          </Link>{" "}
          para continuar.
        </p>
      </EmptyState>
    );
  }
  return <EmptyState title="Você não tem acesso a esta área com seus papéis atuais." />;
}
