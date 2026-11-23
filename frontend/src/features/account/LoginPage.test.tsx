import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { json, renderRoutes } from "../../test/render";
import { LoginPage } from "./LoginPage";

const anonymous = {
  status: 401,
  data: { flows: [{ id: "login" }] },
  meta: { is_authenticated: false },
};
const mfaPending = {
  status: 401,
  data: { flows: [{ id: "login" }, { id: "mfa_authenticate", is_pending: true, types: ["totp"] }] },
  meta: { is_authenticated: false },
};
const authenticated = {
  status: 200,
  data: { user: { id: "u" } },
  meta: { is_authenticated: true },
};

function routes() {
  return [
    { path: "/entrar", element: <LoginPage /> },
    { path: "/contratos", element: <p>Área de contratos</p> },
  ];
}

describe("LoginPage", () => {
  const fetchMock = vi.fn<typeof fetch>();

  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
  });

  it("pede o segundo fator e segue para o destino interno", async () => {
    fetchMock
      .mockResolvedValueOnce(json(401, anonymous))
      .mockResolvedValueOnce(json(401, mfaPending))
      .mockResolvedValueOnce(json(200, authenticated));
    const user = userEvent.setup();
    renderRoutes(routes(), { path: "/entrar?proxima=%2Fcontratos" });

    await user.type(await screen.findByLabelText(/E-mail/), "admin.a@exemplo.test");
    await user.type(screen.getByLabelText(/Senha/), "segredo");
    await user.click(screen.getByRole("button", { name: "Entrar" }));

    const code = await screen.findByLabelText(/Código do aplicativo autenticador/);
    await user.type(code, "123456");
    await user.click(screen.getByRole("button", { name: "Confirmar código" }));

    expect(await screen.findByText("Área de contratos")).toBeInTheDocument();
    const login = fetchMock.mock.calls[1]?.[0];
    expect(login).toBeInstanceOf(Request);
    expect(await (login as Request).json()).toEqual({
      email: "admin.a@exemplo.test",
      password: "segredo",
    });
  });

  it("mostra o erro de credenciais no campo e não troca de etapa", async () => {
    fetchMock.mockResolvedValueOnce(json(401, anonymous)).mockResolvedValueOnce(
      json(400, {
        status: 400,
        errors: [{ message: "E-mail ou senha incorretos.", code: "x", param: "password" }],
      }),
    );
    const user = userEvent.setup();
    renderRoutes(routes(), { path: "/entrar" });

    await user.type(await screen.findByLabelText(/E-mail/), "a@exemplo.test");
    await user.type(screen.getByLabelText(/Senha/), "errada");
    await user.click(screen.getByRole("button", { name: "Entrar" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("E-mail ou senha incorretos.");
    await waitFor(() => {
      expect(screen.getByLabelText(/Senha/)).toHaveAttribute("aria-invalid", "true");
    });
    expect(screen.queryByLabelText(/Código/)).not.toBeInTheDocument();
  });

  it("não redireciona para fora da aplicação", async () => {
    fetchMock.mockResolvedValueOnce(json(200, authenticated));
    const { router } = renderRoutes([...routes(), { path: "/", element: <p>Painel</p> }], {
      path: "/entrar?proxima=https%3A%2F%2Fexemplo.invalid",
    });
    expect(await screen.findByText("Painel")).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/");
  });
});
