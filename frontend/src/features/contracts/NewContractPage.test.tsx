import { screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { json, me, renderRoutes } from "../../test/render";
import { NewContractPage } from "./NewContractPage";

const session = me();
const directory = [
  {
    user_id: "22222222-2222-4222-8222-222222222222",
    full_name: "Administração (fictícia)",
    email: "admin.a@exemplo.test",
  },
  { user_id: session.id, full_name: session.full_name, email: session.email },
];

describe("NewContractPage", () => {
  const fetchMock = vi.fn<typeof fetch>();

  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
  });

  it("mantém o usuário atual como responsável mesmo com o diretório carregando depois", async () => {
    let releaseDirectory: (response: Response) => void = () => undefined;
    fetchMock.mockImplementation((input) => {
      const url = new URL(input instanceof Request ? input.url : String(input));
      if (url.pathname === "/api/v1/members/directory") {
        return new Promise((resolve) => {
          releaseDirectory = resolve;
        });
      }
      if (url.pathname === "/api/v1/departments") return Promise.resolve(json(200, []));
      if (url.pathname === "/api/v1/counterparties") {
        return Promise.resolve(json(200, { next: null, previous: null, results: [] }));
      }
      return Promise.resolve(json(404, {}));
    });
    const active = session.active_organization;
    if (active === null) throw new Error("sessão de teste sem organização");
    const sessionWithCreate = me({
      active_organization: { ...active, permissions: ["contracts.read", "contracts.create"] },
    });
    renderRoutes([{ path: "/", element: <NewContractPage /> }], { session: sessionWithCreate });

    expect(await screen.findByRole("status")).toHaveTextContent("Carregando");
    expect(screen.queryByLabelText(/^Responsável/)).not.toBeInTheDocument();
    releaseDirectory(json(200, directory));

    expect(await screen.findByLabelText(/^Responsável/)).toHaveValue(session.id);
  });
});
