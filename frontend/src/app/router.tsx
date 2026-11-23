import type { ComponentType } from "react";
import { createBrowserRouter, Link, useRouteError } from "react-router";

import { EmptyState, ErrorState } from "../components/states";
import { PageHeader } from "../components/ui";
import { ConfirmEmailPage } from "../features/account/ConfirmEmailPage";
import { LoginPage } from "../features/account/LoginPage";
import {
  PasswordResetPage,
  PasswordResetRequestPage,
} from "../features/account/PasswordResetPages";
import { SignupPage } from "../features/account/SignupPage";
import {
  CounterpartyPage,
  NewCounterpartyPage,
} from "../features/counterparties/CounterpartyForms";
import { DashboardPage } from "../features/dashboard/DashboardPage";
import { AuthGate, OrganizationGate } from "./gates";

/**
 * Páginas carregadas sob demanda: o pacote inicial leva só login, painel e a
 * estrutura da aplicação.
 */
function page<M>(load: () => Promise<M>, pick: (module: M) => ComponentType) {
  return { lazy: async () => ({ Component: pick(await load()) }) };
}

function NotFoundPage() {
  return (
    <>
      <PageHeader title="Página não encontrada" />
      <EmptyState title="O endereço não existe ou você não tem acesso a ele.">
        <p>
          <Link to="/">Voltar ao painel</Link>
        </p>
      </EmptyState>
    </>
  );
}

function RouteError() {
  const error = useRouteError();
  return (
    <main className="content" id="conteudo">
      <ErrorState error={error} />
      <p>
        <Link to="/">Voltar ao painel</Link>
      </p>
    </main>
  );
}

export const router = createBrowserRouter([
  { path: "/entrar", element: <LoginPage /> },
  { path: "/cadastro", element: <SignupPage /> },
  { path: "/conta/confirmar-email/:key", element: <ConfirmEmailPage /> },
  { path: "/conta/redefinir-senha", element: <PasswordResetRequestPage /> },
  { path: "/conta/redefinir-senha/:key", element: <PasswordResetPage /> },
  {
    element: <AuthGate />,
    errorElement: <RouteError />,
    children: [
      {
        path: "/convites/aceitar",
        ...page(
          () => import("../features/account/AcceptInvitationPage"),
          (m) => m.AcceptInvitationPage,
        ),
      },
      {
        element: <OrganizationGate />,
        children: [
          { index: true, element: <DashboardPage /> },
          {
            path: "/conta/seguranca",
            ...page(
              () => import("../features/account/SecurityPage"),
              (m) => m.SecurityPage,
            ),
          },
          {
            path: "/contratos",
            ...page(
              () => import("../features/contracts/ContractsPage"),
              (m) => m.ContractsPage,
            ),
          },
          {
            path: "/contratos/novo",
            ...page(
              () => import("../features/contracts/NewContractPage"),
              (m) => m.NewContractPage,
            ),
          },
          {
            path: "/contratos/:id",
            ...page(
              () => import("../features/contracts/ContractLayout"),
              (m) => m.ContractLayout,
            ),
            children: [
              {
                index: true,
                ...page(
                  () => import("../features/contracts/OverviewTab"),
                  (m) => m.OverviewTab,
                ),
              },
              {
                path: "rascunho",
                ...page(
                  () => import("../features/contracts/DraftTab"),
                  (m) => m.DraftTab,
                ),
              },
              {
                path: "revisoes",
                ...page(
                  () => import("../features/contracts/RevisionsTab"),
                  (m) => m.RevisionsTab,
                ),
              },
              {
                path: "aprovacao",
                ...page(
                  () => import("../features/contracts/ApprovalTab"),
                  (m) => m.ApprovalTab,
                ),
              },
              {
                path: "assinatura",
                ...page(
                  () => import("../features/contracts/SignatureTab"),
                  (m) => m.SignatureTab,
                ),
              },
              {
                path: "vigencia",
                ...page(
                  () => import("../features/contracts/LifecycleTab"),
                  (m) => m.LifecycleTab,
                ),
              },
              {
                path: "prazos",
                ...page(
                  () => import("../features/contracts/DeadlinesTab"),
                  (m) => m.DeadlinesTab,
                ),
              },
              {
                path: "obrigacoes",
                ...page(
                  () => import("../features/contracts/ObligationsTab"),
                  (m) => m.ObligationsTab,
                ),
              },
              {
                path: "valores",
                ...page(
                  () => import("../features/contracts/FinanceTab"),
                  (m) => m.FinanceTab,
                ),
              },
              {
                path: "anexos",
                ...page(
                  () => import("../features/contracts/AttachmentsTab"),
                  (m) => m.AttachmentsTab,
                ),
              },
              {
                path: "acesso",
                ...page(
                  () => import("../features/contracts/AccessTab"),
                  (m) => m.AccessTab,
                ),
              },
            ],
          },
          {
            path: "/solicitacoes",
            ...page(
              () => import("../features/requests/RequestsPages"),
              (m) => m.RequestsPage,
            ),
          },
          {
            path: "/solicitacoes/nova",
            ...page(
              () => import("../features/requests/RequestsPages"),
              (m) => m.NewRequestPage,
            ),
          },
          {
            path: "/solicitacoes/:id",
            ...page(
              () => import("../features/requests/RequestsPages"),
              (m) => m.RequestPage,
            ),
          },
          {
            path: "/contrapartes",
            ...page(
              () => import("../features/counterparties/CounterpartiesPage"),
              (m) => m.CounterpartiesPage,
            ),
          },
          { path: "/contrapartes/nova", element: <NewCounterpartyPage /> },
          { path: "/contrapartes/:id", element: <CounterpartyPage /> },
          {
            path: "/modelos",
            ...page(
              () => import("../features/templates/TemplatesPages"),
              (m) => m.TemplatesPage,
            ),
          },
          {
            path: "/modelos/novo",
            ...page(
              () => import("../features/templates/TemplatesPages"),
              (m) => m.NewTemplatePage,
            ),
          },
          {
            path: "/modelos/:id",
            ...page(
              () => import("../features/templates/TemplatesPages"),
              (m) => m.TemplatePage,
            ),
          },
          {
            path: "/aprovacoes",
            ...page(
              () => import("../features/approvals/ApprovalsPages"),
              (m) => m.ApprovalsPage,
            ),
          },
          {
            path: "/aprovacoes/:id",
            ...page(
              () => import("../features/approvals/ApprovalsPages"),
              (m) => m.RoundPage,
            ),
          },
          {
            path: "/calendario",
            ...page(
              () => import("../features/calendar/CalendarPage"),
              (m) => m.CalendarPage,
            ),
          },
          {
            path: "/pesquisa",
            ...page(
              () => import("../features/search/SearchPage"),
              (m) => m.SearchPage,
            ),
          },
          {
            path: "/relatorios",
            ...page(
              () => import("../features/reports/ReportsPage"),
              (m) => m.ReportsPage,
            ),
          },
          {
            path: "/administracao/usuarios",
            ...page(
              () => import("../features/admin/MembersPage"),
              (m) => m.MembersPage,
            ),
          },
          {
            path: "/administracao/estrutura",
            ...page(
              () => import("../features/admin/StructurePage"),
              (m) => m.StructurePage,
            ),
          },
          {
            path: "/administracao/workflows",
            ...page(
              () => import("../features/admin/WorkflowsPage"),
              (m) => m.WorkflowsPage,
            ),
          },
          {
            path: "/administracao/integracoes",
            ...page(
              () => import("../features/admin/IntegrationsPage"),
              (m) => m.IntegrationsPage,
            ),
          },
          {
            path: "/administracao/operacao",
            ...page(
              () => import("../features/admin/OperationsPage"),
              (m) => m.OperationsPage,
            ),
          },
          {
            path: "/auditoria",
            ...page(
              () => import("../features/audit/AuditPage"),
              (m) => m.AuditPage,
            ),
          },
          { path: "*", element: <NotFoundPage /> },
        ],
      },
    ],
  },
]);
