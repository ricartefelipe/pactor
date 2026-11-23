import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactNode } from "react";
import { createMemoryRouter, RouterProvider, type RouteObject } from "react-router";

import type { Schemas } from "../api/client";
import { SessionProvider } from "../auth/session";
import { NotificationsProvider } from "../components/notifications";

export function me(overrides: Partial<Schemas["Me"]> = {}): Schemas["Me"] {
  return {
    id: "11111111-1111-4111-8111-111111111111",
    email: "gestora.a@exemplo.test",
    full_name: "Gestora (fictícia)",
    mfa_enrolled: false,
    mfa_satisfied: false,
    organizations: [
      {
        id: "org-a",
        name: "Organização A (fictícia)",
        slug: "demo-a",
        kind: "internal",
        roles: ["contract_manager"],
      },
    ],
    active_organization: {
      organization_id: "org-a",
      name: "Organização A (fictícia)",
      kind: "internal",
      roles: ["contract_manager"],
      permissions: ["contracts.read", "contracts.create"],
      mfa_required_for: [],
    },
    role_labels: {},
    ...overrides,
  };
}

export function renderRoutes(
  routes: RouteObject[],
  { path = "/", session }: { path?: string; session?: Schemas["Me"] } = {},
) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  const wrap = (node: ReactNode) =>
    session ? <SessionProvider me={session}>{node}</SessionProvider> : node;
  const view = render(
    <QueryClientProvider client={queryClient}>
      <NotificationsProvider>{wrap(<RouterProvider router={router} />)}</NotificationsProvider>
    </QueryClientProvider>,
  );
  return { ...view, router, queryClient };
}

/** Resposta JSON para simular `fetch`. */
export function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}
