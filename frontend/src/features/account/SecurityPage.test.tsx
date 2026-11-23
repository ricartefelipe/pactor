import { screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { json, me, renderRoutes } from "../../test/render";
import { SecurityPage } from "./SecurityPage";

describe("SecurityPage", () => {
  const fetchMock = vi.fn<typeof fetch>();

  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
  });

  it("mostra QR code e chave para ativar a verificação em duas etapas", async () => {
    fetchMock.mockResolvedValueOnce(
      json(404, {
        status: 404,
        meta: {
          secret: "JBSWY3DPEHPK3PXP",
          totp_url: "otpauth://totp/Pactor:x?secret=JBSWY3DPEHPK3PXP",
        },
      }),
    );
    renderRoutes([{ path: "/", element: <SecurityPage /> }], { session: me() });
    expect(
      await screen.findByRole("img", { name: /QR code para cadastrar o Pactor/ }),
    ).toBeInTheDocument();
    expect(screen.getByText("JBSWY3DPEHPK3PXP")).toBeInTheDocument();
    expect(screen.getByLabelText(/Código do aplicativo/)).toBeRequired();
  });

  it("não mostra QR code quando a verificação já está ativa", async () => {
    fetchMock.mockResolvedValueOnce(json(200, { status: 200, data: { type: "totp" } }));
    renderRoutes([{ path: "/", element: <SecurityPage /> }], {
      session: me({ mfa_enrolled: true, mfa_satisfied: true }),
    });
    expect(await screen.findByText(/já foi confirmado nesta sessão/)).toBeInTheDocument();
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
  });
});
