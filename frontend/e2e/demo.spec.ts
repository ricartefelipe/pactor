import { expect, test, type Page } from "@playwright/test";

import { cnpj, expectAccessible, isoDate, login, logout, mailsMatching } from "./support";

/**
 * Roteiro demonstrativo (docs/DEMONSTRACAO.md) pela interface, com a pilha
 * completa: workers enviam ao simulador, processam arquivos e indexam a
 * pesquisa; o agendador dispara os alertas.
 */

const RUN = Date.now();
const TERM = `xilofone${String(RUN).slice(-6)}`;
const COUNTERPARTY = `Fornecedora Roteiro ${String(RUN).slice(-6)} Ltda (fictícia)`;
const REQUEST_TITLE = `Suporte de campo ${String(RUN).slice(-6)} (fictício)`;
const DEADLINE_TITLE = `Decisão de renovação ${String(RUN).slice(-6)}`;

async function openTab(page: Page, contractUrl: string, tab: string): Promise<void> {
  await page.goto(contractUrl);
  const link = page
    .getByRole("navigation", { name: "Seções do contrato" })
    .getByRole("link", { name: tab });
  await link.click();
  // A aba é carregada sob demanda: até lá, a anterior continua na tela.
  await expect(link).toHaveAttribute("aria-current", "page");
  await expect(page.locator("main")).not.toHaveAttribute("aria-busy", "true");
}

async function notice(page: Page, text: string | RegExp): Promise<void> {
  await expect(page.locator(".notices").getByText(text).first()).toBeVisible();
}

async function approveAs(page: Page, reference: string): Promise<void> {
  await login(page, "aprovador.a@exemplo.test");
  await page
    .getByRole("navigation", { name: "Principal" })
    .getByRole("link", { name: "Aprovações" })
    .click();
  await page
    .getByRole("link", { name: new RegExp(reference) })
    .first()
    .click();
  await page.getByLabel(/^Decisão/).selectOption({ label: "Aprovar" });
  await page.getByRole("button", { name: "Registrar voto" }).click();
  await notice(page, "Voto registrado.");
  await expect(page.getByText("Aprovada").first()).toBeVisible();
  await logout(page);
}

async function editAndPublish(
  page: Page,
  contractUrl: string,
  extra: string,
  revision: number,
): Promise<void> {
  await openTab(page, contractUrl, "Rascunho");
  const content = page.getByLabel("Conteúdo");
  await content.fill(`${await content.inputValue()}\n${extra}`);
  await page.getByRole("button", { name: "Salvar rascunho" }).click();
  await notice(page, "Rascunho salvo.");
  await page.waitForLoadState("networkidle");
  await page.getByLabel("Resumo das alterações").fill(`Revisão ${String(revision)} do roteiro`);
  await page.getByRole("button", { name: "Publicar rascunho salvo como revisão" }).click();
  await notice(page, `Revisão ${String(revision)} publicada.`);
}

test("roteiro demonstrativo em 15 passos", async ({ page }) => {
  let contractUrl = "";
  let reference = "";

  await test.step("1. Entrar na Organização A", async () => {
    await login(page, "gestora.a@exemplo.test");
    await expect(page.getByText("Organização A (fictícia)").first()).toBeVisible();
    await expectAccessible(page);
  });

  await test.step("2. Cadastrar contraparte", async () => {
    await page.goto("/contrapartes/nova");
    await page.getByLabel(/^Tipo/).selectOption("company");
    await page.getByLabel(/^Nome ou razão social/).fill(COUNTERPARTY);
    await page.getByLabel(/^CPF ou CNPJ/).fill(cnpj(RUN));
    await expectAccessible(page);
    await page.getByRole("button", { name: "Cadastrar" }).click();
    await notice(page, "Contraparte cadastrada.");
    await expect(page.getByRole("heading", { level: 1, name: COUNTERPARTY })).toBeVisible();
  });

  await test.step("3. Criar solicitação e convertê-la em contrato", async () => {
    await page.goto("/solicitacoes/nova");
    await page.getByLabel(/^Título/).fill(REQUEST_TITLE);
    await page.getByLabel(/^Tipo de contrato/).selectOption("services");
    await page.getByLabel(/^Objetivo/).fill("Atendimento técnico mensal (cenário fictício).");
    await page.getByLabel(/^Contraparte/).selectOption({ label: COUNTERPARTY });
    await page.getByLabel(/^Valor estimado/).fill("36000.00");
    await page.getByLabel(/^Início desejado/).fill(isoDate(0));
    await page.getByLabel(/^Fim desejado/).fill(isoDate(365));
    await page.getByRole("button", { name: "Enviar solicitação" }).click();
    await notice(page, /Solicitação .* enviada\./);
    await page.getByRole("button", { name: "Iniciar triagem" }).click();
    await notice(page, "Solicitação em triagem.");
    await page.getByRole("button", { name: "Converter em contrato" }).click();
    const dialog = page.getByRole("dialog", { name: "Converter em contrato" });
    await dialog.getByRole("button", { name: "Criar contrato" }).click();
    await notice(page, /Contrato .* criado a partir da solicitação\./);
    await expect(page).toHaveURL(/\/contratos\/[0-9a-f-]{36}$/);
    contractUrl = new URL(page.url()).pathname;
    const title = await page.getByRole("heading", { level: 1 }).innerText();
    reference = title.split(" · ")[0] ?? "";
    expect(reference).not.toBe("");
  });

  await test.step("4. Produzir revisão a partir de modelo", async () => {
    await openTab(page, contractUrl, "Rascunho");
    await page
      .getByLabel(/^Modelo e versão/)
      .selectOption({ label: "Prestação de serviços (modelo demonstrativo) — versão 1" });
    await page.getByLabel(/^Contratante/).fill("Organização A (fictícia)");
    await page.getByLabel(/^Contratada/).fill(COUNTERPARTY);
    await page.getByLabel(/^Valor mensal/).fill("3000.00");
    await page.getByLabel(/^Início/).fill(isoDate(0));
    await page.getByRole("button", { name: "Gerar e publicar revisão" }).click();
    await notice(page, "Revisão 1 gerada a partir do modelo.");
  });

  await test.step("5. Registrar comentário e nova revisão", async () => {
    await openTab(page, contractUrl, "Revisões e comentários");
    await page.getByLabel(/^Novo comentário/).fill("Incluir cláusula de atendimento emergencial.");
    await page.getByRole("button", { name: "Comentar" }).click();
    await notice(page, "Comentário registrado.");
    await expectAccessible(page);
    await editAndPublish(
      page,
      contractUrl,
      `Cláusula extra: atendimento emergencial com ${TERM}.`,
      2,
    );
    await openTab(page, contractUrl, "Revisões e comentários");
    await page.getByLabel("Revisão anterior").selectOption({ label: "Revisão 1" });
    await page.getByLabel("Revisão nova").selectOption({ label: "Revisão 2" });
    await page.getByRole("button", { name: "Comparar" }).click();
    await expect(page.getByLabel("Diferenças entre revisões")).toContainText(TERM);
  });

  await test.step("6. Aprovar a versão correta", async () => {
    await openTab(page, contractUrl, "Aprovação");
    await page.getByRole("button", { name: "Enviar revisão 2 para aprovação" }).click();
    await notice(page, "Revisão enviada para aprovação.");
    await logout(page);
    await approveAs(page, reference);
  });

  await test.step("7. Outra versão depois da aprovação é bloqueada para assinatura", async () => {
    await login(page, "gestora.a@exemplo.test");
    await editAndPublish(page, contractUrl, "Ajuste após a aprovação.", 3);
    await openTab(page, contractUrl, "Assinatura");
    await expect(
      page.getByText("O envio para assinatura exige uma revisão aprovada."),
    ).toBeVisible();
    await expect(page.getByRole("button", { name: "Enviar para assinatura" })).toHaveCount(0);
    await openTab(page, contractUrl, "Aprovação");
    await page.getByRole("button", { name: "Enviar revisão 3 para aprovação" }).click();
    await notice(page, "Revisão enviada para aprovação.");
    await logout(page);
    await approveAs(page, reference);
    await login(page, "gestora.a@exemplo.test");
  });

  await test.step("8. Enviar para o simulador de assinatura", async () => {
    await openTab(page, contractUrl, "Assinatura");
    const signers = [
      ["Signatária Um (fictícia)", "signataria.um@exemplo.test"],
      ["Signatário Dois (fictício)", "signatario.dois@exemplo.test"],
    ] as const;
    for (const [index, [name, email]] of signers.entries()) {
      const group = page.getByRole("group", { name: `Signatário ${String(index + 1)}` });
      await group.getByLabel(/^Nome/).fill(name);
      await group.getByLabel(/^E-mail/).fill(email);
    }
    await page.getByRole("button", { name: "Enviar para assinatura" }).click();
    await notice(page, /Envelope criado/);
    await expect(
      page.getByRole("heading", { name: /Envelope da revisão 3 · Enviado/ }),
    ).toBeVisible({ timeout: 60_000 });
  });

  await test.step("9. Confirmar a conclusão por webhook", async () => {
    await page.getByRole("button", { name: "Assinar como Signatária Um (fictícia)" }).click();
    await notice(page, "Evento entregue por webhook: aplicado.");
    await page.getByRole("button", { name: "Assinar como Signatário Dois (fictício)" }).click();
    await expect(
      page.getByRole("heading", { name: /Envelope da revisão 3 · Concluído/ }),
    ).toBeVisible();
    await expect(page.getByText("Assinado").first()).toBeVisible();
  });

  await test.step("10. Repetir o evento sem duplicar efeitos", async () => {
    await page.getByRole("button", { name: "Reenviar último evento" }).click();
    await expect(
      page.getByText("Evento repetido reconhecido como duplicado: nenhum efeito novo.").first(),
    ).toBeVisible();
    await expect(
      page.getByRole("heading", { name: /Envelope da revisão 3 · Concluído/ }),
    ).toHaveCount(1);
  });

  await test.step("11. Ativar a vigência conforme a data", async () => {
    await openTab(page, contractUrl, "Vigência");
    await expect(page.locator(".definition-list").getByText("Vigente")).toBeVisible();
    await expect(page.getByText(/Não iniciado → Vigente/)).toBeVisible();
  });

  await test.step("12. Criar obrigação e prazo de renovação", async () => {
    await openTab(page, contractUrl, "Obrigações");
    await page.getByLabel(/^Título/).fill("Relatório mensal de atendimentos");
    await page.getByLabel(/^Categoria/).selectOption("report");
    await page.getByLabel(/^Primeiro vencimento/).fill(isoDate(30));
    await page.getByLabel(/^Número de ocorrências/).fill("3");
    await page.getByRole("button", { name: "Criar obrigação" }).click();
    await notice(page, "Obrigação criada com suas ocorrências.");
    await expect(
      page.getByRole("heading", { name: "Relatório mensal de atendimentos" }),
    ).toBeVisible();

    await openTab(page, contractUrl, "Prazos e renovação");
    await page.getByLabel(/^Tipo/).selectOption("renewal_decision");
    await page.getByLabel(/^Título/).fill(DEADLINE_TITLE);
    await page.getByLabel(/^Referência/).selectOption("fixed");
    await page.getByLabel(/^Data/).fill(isoDate(10));
    // O lembrete (15 dias antes) já está no passado: o agendador o dispara no próximo ciclo.
    await page.getByLabel(/^Avisar com antecedência/).fill("15");
    await page.getByRole("button", { name: "Criar prazo" }).click();
    await notice(page, "Prazo criado e alertas programados.");
    await expect(page.getByRole("cell", { name: DEADLINE_TITLE })).toBeVisible();
    await page.getByLabel(/^Modo/).selectOption("manual");
    await page.getByRole("button", { name: "Salvar política" }).click();
    await notice(page, "Política de renovação registrada.");
    await expectAccessible(page);
  });

  await test.step("13. Disparar alerta sem duplicidade", async () => {
    await expect
      .poll(async () => (await mailsMatching(DEADLINE_TITLE)).length, {
        timeout: 150_000,
        intervals: [5_000],
      })
      .toBe(1);
    // Mais de um ciclo do agendador depois, continua um único e-mail.
    await page.waitForTimeout(75_000);
    const mails = await mailsMatching(DEADLINE_TITLE);
    expect(mails).toHaveLength(1);
    expect(mails[0]?.To.map((to) => to.Address)).toEqual(["gestora.a@exemplo.test"]);
  });

  await test.step("14. Pesquisar o contrato com autorização", async () => {
    await page.goto("/pesquisa");
    await page.getByLabel(/^Termos ou referência/).fill(TERM);
    await expect(async () => {
      await page.getByRole("button", { name: "Pesquisar" }).click();
      await expect(page.getByText("1 contrato encontrado")).toBeVisible({ timeout: 2_000 });
    }).toPass({ timeout: 60_000 });
    await expect(page.locator("mark").filter({ hasText: TERM }).first()).toBeVisible();
    await expectAccessible(page);
  });

  await test.step("15. Entrar na Organização B e comprovar isolamento", async () => {
    await logout(page);
    await login(page, "gestora.b@exemplo.test");
    await expect(page.getByText("Organização B (fictícia)").first()).toBeVisible();
    await page.goto(`/pesquisa?q=${TERM}`);
    await expect(page.getByText("0 contratos encontrados")).toBeVisible();
    await page.goto(contractUrl);
    await expect(page.getByRole("alert")).toContainText(/não encontrad/i);
    await page.goto("/contrapartes");
    await expect(page.getByText(COUNTERPARTY)).toHaveCount(0);
  });
});

test("administração exige o segundo fator e respeita acessibilidade", async ({ page }) => {
  await login(page, "admin.a@exemplo.test", { mfa: true });
  await page
    .getByRole("navigation", { name: "Principal" })
    .getByRole("link", { name: "Usuários e convites" })
    .click();
  await expect(page.getByRole("heading", { level: 1, name: "Usuários e convites" })).toBeVisible();
  await expect(
    page
      .getByRole("table", { name: "Membros da organização" })
      .getByRole("cell", { name: "gestora.a@exemplo.test" }),
  ).toBeVisible();
  await expectAccessible(page);
  await page
    .getByRole("navigation", { name: "Principal" })
    .getByRole("link", { name: "Auditoria" })
    .click();
  await expect(page.getByRole("table", { name: "Eventos de auditoria" })).toBeVisible();
  await expectAccessible(page);
});
