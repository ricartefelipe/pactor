import {
  Activity,
  Building2,
  CalendarDays,
  ChartColumn,
  CircleCheck,
  Files,
  FileText,
  Handshake,
  Inbox,
  LayoutDashboard,
  LogOut,
  Network,
  Plug,
  ScrollText,
  Search,
  ShieldCheck,
  Users,
  Workflow,
  type LucideIcon,
} from "lucide-react";
import { useEffect, useRef, useState, type ChangeEvent } from "react";
import { Link, NavLink, Outlet, useLocation, useNavigate, useNavigation } from "react-router";

import { errorMessage } from "../api/errors";
import { useActiveOrganization, useSession, type Permission } from "../auth/session";
import { Brand } from "../components/Brand";
import { useNotify } from "../components/notifications";
import { initials } from "../lib/format";

interface NavItem {
  to: string;
  label: string;
  icon: LucideIcon;
  /** Visível com qualquer uma destas permissões (concedida ou pendente de MFA). */
  anyOf?: readonly Permission[];
}

const WORK: readonly NavItem[] = [
  { to: "/", label: "Painel", icon: LayoutDashboard },
  { to: "/contratos", label: "Contratos", icon: FileText, anyOf: ["contracts.read"] },
  {
    to: "/solicitacoes",
    label: "Solicitações",
    icon: Inbox,
    anyOf: ["requests.create", "requests.manage"],
  },
  { to: "/contrapartes", label: "Contrapartes", icon: Handshake, anyOf: ["counterparties.read"] },
  { to: "/modelos", label: "Modelos", icon: Files, anyOf: ["templates.manage"] },
  { to: "/aprovacoes", label: "Aprovações", icon: CircleCheck, anyOf: ["approvals.vote"] },
  { to: "/calendario", label: "Calendário", icon: CalendarDays, anyOf: ["contracts.read"] },
  { to: "/pesquisa", label: "Pesquisa", icon: Search, anyOf: ["contracts.read"] },
  { to: "/relatorios", label: "Relatórios", icon: ChartColumn, anyOf: ["reports.read"] },
];

const ADMINISTRATION: readonly NavItem[] = [
  {
    to: "/administracao/usuarios",
    label: "Usuários e convites",
    icon: Users,
    anyOf: ["members.manage"],
  },
  {
    to: "/administracao/estrutura",
    label: "Departamentos e equipes",
    icon: Network,
    anyOf: ["members.manage"],
  },
  {
    to: "/administracao/workflows",
    label: "Workflows de aprovação",
    icon: Workflow,
    anyOf: ["workflows.manage"],
  },
  {
    to: "/administracao/integracoes",
    label: "Integrações",
    icon: Plug,
    anyOf: ["integrations.manage"],
  },
  {
    to: "/administracao/operacao",
    label: "Operação",
    icon: Activity,
    anyOf: ["operations.reprocess"],
  },
  { to: "/auditoria", label: "Auditoria", icon: ScrollText, anyOf: ["audit.read"] },
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
              <item.icon aria-hidden="true" size={18} strokeWidth={1.8} />
              <span>{item.label}</span>
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
    return (
      <span className="topbar__organization">
        <Building2 aria-hidden="true" size={18} />
        <span>{active.name}</span>
      </span>
    );
  }
  return (
    <div className="topbar__organization">
      <Building2 aria-hidden="true" size={18} />
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
  // Páginas carregadas sob demanda: enquanto a próxima carrega, a atual continua
  // visível e marcada como ocupada.
  const navigating = useNavigation().state !== "idle";

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
      <div className="shell">
        <aside className="sidebar">
          <Link to="/" className="sidebar__brand" aria-label="Pactor: ir para o painel">
            <Brand />
          </Link>
          <nav aria-label="Principal">
            <NavList items={WORK} label="Trabalho" />
            <NavList items={ADMINISTRATION} label="Administração" />
          </nav>
        </aside>
        <div className="workspace">
          <header className="topbar">
            <OrganizationSwitcher />
            <nav aria-label="Conta" className="topbar__account">
              <span className="topbar__user">
                <span className="avatar" aria-hidden="true">
                  {initials(me.full_name, me.email)}
                </span>
                <span className="topbar__name">{me.full_name || me.email}</span>
              </span>
              <Link to="/conta/seguranca" className="topbar__action">
                <ShieldCheck aria-hidden="true" size={18} />
                <span>Segurança</span>
              </Link>
              <button type="button" className="topbar__action" onClick={() => void handleSignOut()}>
                <LogOut aria-hidden="true" size={18} />
                <span>Sair</span>
              </button>
            </nav>
          </header>
          <main id="conteudo" ref={main} tabIndex={-1} className="content" aria-busy={navigating}>
            {navigating && (
              <p className="navigation-progress" role="status">
                Abrindo página…
              </p>
            )}
            <Outlet />
          </main>
        </div>
      </div>
    </>
  );
}
