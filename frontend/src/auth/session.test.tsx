import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactElement } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AuthGate } from "../app/gates";
import { Layout } from "../app/Layout";
import { json, me, renderRoutes } from "../test/render";
import { ME_KEY, useSession } from "./session";

function Switcher() {
  const { switchOrganization, active } = useSession();
  return (
    <>
      <p>Ativa: {active?.name}</p>
      <button type="button" onClick={() => void switchOrganization("org-b")}>
        Trocar
      </button>
    </>
  );
}

const twoOrganizations = me({
  organizations: [
    {
      id: "org-a",
      name: "Organização A (fictícia)",
      slug: "a",
      kind: "internal",
      roles: ["contract_manager"],
    },
    {
      id: "org-b",
      name: "Organização B (fictícia)",
      slug: "b",
      kind: "internal",
      roles: ["contract_manager"],
    },
  ],
});

describe("sessão", () => {
  const fetchMock = vi.fn<typeof fetch>();

  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
  });

  it("descarta o cache e mostra a nova organização ao trocar", async () => {
    const switched = me({
      ...twoOrganizations,
      active_organization: {
        organization_id: "org-b",
        name: "Organização B (fictícia)",
        kind: "internal",
        roles: ["contract_manager"],
        permissions: ["contracts.read"],
        mfa_required_for: [],
      },
    });
    fetchMock
      .mockResolvedValueOnce(json(200, { status: 200, data: {}, meta: { is_authenticated: true } }))
      .mockResolvedValueOnce(json(200, twoOrganizations))
      .mockResolvedValueOnce(json(200, switched));
    const user = userEvent.setup();
    const { queryClient } = renderRoutes([
      { element: <AuthGate />, children: [{ path: "/", element: <Switcher /> }] },
    ]);
    expect(await screen.findByText("Ativa: Organização A (fictícia)")).toBeInTheDocument();
    queryClient.setQueryData(["org", "org-a", "contracts", "list"], { results: ["contrato da A"] });

    await user.click(screen.getByRole("button", { name: "Trocar" }));

    expect(await screen.findByText("Ativa: Organização B (fictícia)")).toBeInTheDocument();
    expect(queryClient.getQueryData(["org", "org-a", "contracts", "list"])).toBeUndefined();
    expect(queryClient.getQueryData(ME_KEY)).toEqual(switched);
    const request = fetchMock.mock.calls[2]?.[0];
    expect(request).toBeInstanceOf(Request);
    expect((request as Request).method).toBe("PUT");
    expect(await (request as Request).json()).toEqual({ organization_id: "org-b" });
  });

  it("mostra na navegação só as áreas permitidas ou pendentes de segundo fator", async () => {
    const session = me({
      active_organization: {
        organization_id: "org-a",
        name: "Organização A (fictícia)",
        kind: "internal",
        roles: ["admin"],
        permissions: ["audit.read"],
        mfa_required_for: ["members.manage"],
      },
    });
    renderRoutes(
      [{ path: "/", element: <Layout />, children: [{ index: true, element: <p>Início</p> }] }],
      {
        session,
      },
    );
    const navigation = await screen.findByRole("navigation", { name: "Principal" });
    expect(navigation).toHaveTextContent("Auditoria");
    expect(navigation).toHaveTextContent("Usuários e convites");
    expect(navigation).not.toHaveTextContent("Contratos");
    expect(navigation).not.toHaveTextContent("Integrações");
    expect(screen.getByRole("link", { name: "Ir para o conteúdo" })).toHaveAttribute(
      "href",
      "#conteudo",
    );
  });
});

describe("navegação para páginas carregadas sob demanda", () => {
  it("marca a página atual como ocupada até a próxima carregar", async () => {
    let finish: (module: { Component: () => ReactElement }) => void = () => undefined;
    const user = userEvent.setup();
    renderRoutes(
      [
        {
          path: "/",
          element: <Layout />,
          children: [
            { index: true, element: <p>Início</p> },
            {
              path: "contratos",
              lazy: () =>
                new Promise<{ Component: () => ReactElement }>((resolve) => {
                  finish = resolve;
                }),
            },
          ],
        },
      ],
      { session: me() },
    );
    await user.click(await screen.findByRole("link", { name: "Contratos" }));

    expect(await screen.findByText("Abrindo página…")).toBeInTheDocument();
    expect(screen.getByRole("main")).toHaveAttribute("aria-busy", "true");
    expect(screen.getByText("Início")).toBeInTheDocument();

    finish({ Component: () => <p>Lista de contratos</p> });
    expect(await screen.findByText("Lista de contratos")).toBeInTheDocument();
    expect(screen.getByRole("main")).toHaveAttribute("aria-busy", "false");
  });
});
