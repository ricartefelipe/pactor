import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { me, renderRoutes } from "../test/render";
import { CheckboxGroup, SelectField, TextField, checkedValues } from "./forms";
import { Restricted } from "./Restricted";

describe("Restricted", () => {
  it("mostra o conteúdo com a permissão concedida", async () => {
    renderRoutes(
      [
        {
          path: "/",
          element: (
            <Restricted permission="contracts.read">
              <p>Lista</p>
            </Restricted>
          ),
        },
      ],
      {
        session: me(),
      },
    );
    expect(await screen.findByText("Lista")).toBeInTheDocument();
  });

  it("explica quando falta o segundo fator", async () => {
    const session = me({
      active_organization: {
        organization_id: "org-a",
        name: "A",
        kind: "internal",
        roles: ["admin"],
        permissions: [],
        mfa_required_for: ["members.manage"],
      },
    });
    renderRoutes(
      [
        {
          path: "/x",
          element: (
            <Restricted permission="members.manage">
              <p>Membros</p>
            </Restricted>
          ),
        },
      ],
      {
        path: "/x",
        session,
      },
    );
    expect(await screen.findByText(/exige verificação em duas etapas/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Segurança" })).toHaveAttribute(
      "href",
      "/conta/seguranca?proxima=%2Fx",
    );
    expect(screen.queryByText("Membros")).not.toBeInTheDocument();
  });

  it("nega sem a permissão", async () => {
    renderRoutes(
      [
        {
          path: "/",
          element: (
            <Restricted permission="audit.read">
              <p>Auditoria</p>
            </Restricted>
          ),
        },
      ],
      {
        session: me(),
      },
    );
    expect(await screen.findByText(/não tem acesso/)).toBeInTheDocument();
  });
});

describe("campos de formulário", () => {
  it("associa rótulo, dica e erro ao campo", async () => {
    renderRoutes([
      {
        path: "/",
        element: (
          <>
            <TextField
              label="Título"
              name="title"
              required
              hint="Até 200 caracteres."
              error="Obrigatório."
            />
            <SelectField
              label="Tipo"
              name="kind"
              placeholder="Selecione"
              options={[{ value: "a", label: "A" }]}
            />
          </>
        ),
      },
    ]);
    const input = await screen.findByLabelText(/Título/);
    expect(input).toBeRequired();
    expect(input).toHaveAttribute("aria-invalid", "true");
    expect(input).toHaveAccessibleDescription("Até 200 caracteres. Obrigatório.");
    expect(screen.getByLabelText("Tipo")).toHaveDisplayValue("Selecione");
  });

  it("lê várias escolhas marcadas", async () => {
    const user = userEvent.setup();
    renderRoutes([
      {
        path: "/",
        element: (
          <form aria-label="papéis">
            <CheckboxGroup
              legend="Papéis"
              name="roles"
              options={[
                { value: "a", label: "Alfa" },
                { value: "b", label: "Beta" },
              ]}
              defaultValues={["a"]}
            />
          </form>
        ),
      },
    ]);
    await user.click(await screen.findByLabelText("Beta"));
    const form = screen.getByRole("form", { name: "papéis" });
    expect(form).toBeInstanceOf(HTMLFormElement);
    expect(checkedValues(form as HTMLFormElement, "roles")).toEqual(["a", "b"]);
    expect(screen.getByRole("group", { name: "Papéis" })).toBeInTheDocument();
  });
});
