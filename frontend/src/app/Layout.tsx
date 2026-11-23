import { useEffect, useRef, useState, type ChangeEvent } from "react";
import { Link, NavLink, Outlet, useLocation, useNavigate } from "react-router";

import { errorMessage } from "../api/errors";
import { useActiveOrganization, useSession, type Permission } from "../auth/session";
import { useNotify } from "../components/notifications";

interface NavItem {
  to: string;
  label: string;
  /** Visível com qualquer uma destas permissões (concedida ou pendente de MFA). */
  anyOf?: readonly Permission[];
}

const WORK: readonly NavItem[] = [
  { to: "/", label: "Painel" },
  { to: "/contratos", label: "Contratos", anyOf: ["contracts.read"] },
  { to: "/solicitacoes", label: "Solicitações", anyOf: ["requests.create", "requests.manage"] },
  { to: "/contrapartes", label: "Contrapartes", anyOf: ["counterparties.read"] },
  { to: "/modelos", label: "Modelos", anyOf: ["templates.manage"] },
  { to: "/aprovacoes", label: "Aprovações", anyOf: ["approvals.vote"] },
  { to: "/calendario", label: "Calendário", anyOf: ["contracts.read"] },
  { to: "/pesquisa", label: "Pesquisa", anyOf: ["contracts.read"] },
  { to: "/relatorios", label: "Relatórios", anyOf: ["reports.read"] },
];

const ADMINISTRATION: readonly NavItem[] = [
  { to: "/administracao/usuarios", label: "Usuários e convites", anyOf: ["members.manage"] },
  { to: "/administracao/estrutura", label: "Departamentos e equipes", anyOf: ["members.manage"] },
  { to: "/administracao/workflows", label: "Workflows de aprovação", anyOf: ["workflows.manage"] },
  { to: "/administracao/integracoes", label: "Integrações", anyOf: ["integrations.manage"] },
  { to: "/administracao/operacao", label: "Operação", anyOf: ["operations.reprocess"] },
  { to: "/auditoria", label: "Auditoria", anyOf: ["audit.read"] },
];

function NavList({ items, label }: { items: readonly NavItem[]; label: string }) {
  const { can, needsMfa } = useSession();
  const visible = items.filter(
    (item) => item.anyOf === undefined || item.anyOf.some((p) => can(p) || needsMfa(p)),
  );
  if (visible.length === 0) return null;
  return (
    <>
      <h2 className="sidebar__title">{label}</h2>
      <ul>
        {visible.map((item) => (
          <li key={item.to}>
            <NavLink to={item.to} end={item.to === "/"}>
              {item.label}
            </NavLink>
          </li>
        ))}
      </ul>
    </>
  );
}

function OrganizationSwitcher() {
  const { me, switchOrganization } = useSession();
  const active = useActiveOrganization();
  const navigate = useNavigate();
  const notify = useNotify();
  const [busy, setBusy] = useState(false);

  async function handleChange(event: ChangeEvent<HTMLSelectElement>) {
    setBusy(true);
    try {
      await switchOrganization(event.target.value);
      await navigate("/");
    } catch (error) {
      notify.error(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  if (me.organizations.length < 2) {
    return <span className="topbar__organization">{active.name}</span>;
  }
  return (
    <div className="topbar__organization">
      <label htmlFor="organizacao-ativa" className="visually-hidden">
        Organização ativa
      </label>
      <select
        id="organizacao-ativa"
        value={active.organization_id}
        disabled={busy}
        onChange={(event) => void handleChange(event)}
      >
        {me.organizations.map((organization) => (
          <option key={organization.id} value={organization.id}>
            {organization.name}
          </option>
        ))}
      </select>
    </div>
  );
}

export function Layout() {
  const { me, signOut } = useSession();
  const location = useLocation();
  const navigate = useNavigate();
  const main = useRef<HTMLElement>(null);

  // Ao navegar, o foco vai para o conteúdo, como numa troca de página tradicional.
  useEffect(() => {
    main.current?.focus();
  }, [location.pathname]);

  async function handleSignOut() {
    await signOut();
    await navigate("/entrar");
  }

  return (
    <>
      <a className="skip-link" href="#conteudo">
        Ir para o conteúdo
      </a>
      <header className="topbar">
        <Link to="/" className="topbar__brand">
          Pactor
        </Link>
        <OrganizationSwitcher />
        <nav aria-label="Conta" className="topbar__account">
          <span className="topbar__user">{me.full_name || me.email}</span>
          <Link to="/conta/seguranca">Segurança</Link>
          <button
            type="button"
            className="button button--link"
            onClick={() => void handleSignOut()}
          >
            Sair
          </button>
        </nav>
      </header>
      <div className="shell">
        <nav aria-label="Principal" className="sidebar">
          <NavList items={WORK} label="Trabalho" />
          <NavList items={ADMINISTRATION} label="Administração" />
        </nav>
        <main id="conteudo" ref={main} tabIndex={-1} className="content">
          <Outlet />
        </main>
      </div>
    </>
  );
}
