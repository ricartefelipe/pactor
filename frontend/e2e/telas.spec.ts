import { expect, test, type Page } from "@playwright/test";

import { expectAccessible, isoDate, login, samplePdf } from "./support";

/**
 * Telas fora do roteiro demonstrativo: estrutura, convites, workflows,
 * integrações, operação, auditoria, contratos (dados, acesso, anexos e
 * valores), modelos, solicitações, contrapartes, calendário e relatórios.
 */

const RUN = String(Date.now()).slice(-6);
const SEED_COUNTERPARTY = "Fornecedora Exemplo A Ltda (fictícia)";

async function notice(page: Page, text: string | RegExp): Promise<void> {
  await expect(page.locator(".notices").getByText(text).first()).toBeVisible();
}

async function openNav(page: Page, label: string): Promise<void> {
  await page
    .getByRole("navigation", { name: "Principal" })
    .getByRole("link", { name: label })
    .click();
  await expect(page.getByRole("heading", { level: 1, name: label })).toBeVisible();
}

async function openTab(page: Page, contractUrl: string, tab: string): Promise<void> {
  await page.goto(contractUrl);
  const link = page
    .getByRole("navigation", { name: "Seções do contrato" })
    .getByRole("link", { name: tab });
  await link.click();
  await expect(link).toHaveAttribute("aria-current", "page");
  await expect(page.locator("main")).not.toHaveAttribute("aria-busy", "true");
}

test("administração: estrutura, convites, workflow, integrações, operação e auditoria", async ({
  page,
}) => {
  await login(page, "admin.a@exemplo.test", { mfa: true });

  await test.step("departamentos e equipes", async () => {
    await openNav(page, "Departamentos e equipes");
    const departments = page.getByRole("region", { name: "Departamentos" });
    await departments.getByLabel(/^Nome/).fill(`Compras ${RUN}`);
    await departments.getByRole("button", { name: "Adicionar" }).click();
    await notice(page, "Departamentos: item criado.");
    await expect(departments.getByText(`Compras ${RUN}`)).toBeVisible();
    const teams = page.getByRole("region", { name: "Equipes" });
    await teams.getByLabel(/^Nome/).fill(`Equipe ${RUN}`);
    await teams.getByRole("button", { name: "Adicionar" }).click();
    await expect(teams.getByText(`Equipe ${RUN}`)).toBeVisible();
    await expectAccessible(page);
  });

  await test.step("convite enviado e revogado", async () => {
    await openNav(page, "Usuários e convites");
    const invitations = page.getByRole("region", { name: "Convites" });
    const email = `convidada.${RUN}@exemplo.test`;
    await invitations.getByLabel(/^E-mail/).fill(email);
    await invitations.getByRole("group", { name: "Papéis" }).getByLabel("Revisor").check();
    await invitations.getByRole("button", { name: "Enviar convite" }).click();
    await notice(page, `Convite enviado para ${email}.`);
    const row = invitations.getByRole("row").filter({ hasText: email });
    await row.getByRole("button", { name: "Revogar" }).click();
    await notice(page, "Convite revogado.");
    await expect(row).toContainText("Revogado");
    await expectAccessible(page);
  });

  await test.step("nova versão do workflow de aprovação", async () => {
    await openNav(page, "Workflows de aprovação");
    await page.getByText("Diretório de membros (ids)").click();
    const entry = await page
      .getByRole("listitem")
      .filter({ hasText: "Aprovador jurídico" })
      .innerText();
    const approver = /[0-9a-f-]{36}/.exec(entry)?.[0];
    expect(approver).toBeDefined();
    const rules = [
      {
        name: "Padrão",
        conditions: [],
        steps: [
          { key: "juridico", name: "Jurídico", order: 1, quorum: 1, approver_ids: [approver] },
        ],
      },
    ];
    await page.getByLabel(/^Regras \(JSON\)/).fill(JSON.stringify(rules));
    await page.getByRole("button", { name: "Publicar" }).click();
    await notice(page, /Workflow publicado na versão \d+/);
    await expect(page.getByRole("table", { name: "Workflows" }).getByText("Ativo")).toHaveCount(1);
    await expectAccessible(page);
  });

  await test.step("integração emitida e revogada", async () => {
    await openNav(page, "Integrações");
    const name = `ERP ${RUN} (fictício)`;
    const form = page.getByRole("region", { name: "Nova integração" });
    await form.getByLabel(/^Nome/).fill(name);
    await form.getByRole("group", { name: "Papéis" }).getByLabel("Revisor").check();
    await form.getByRole("group", { name: "Escopos" }).getByLabel("Ler contratos").check();
    await form.getByRole("button", { name: "Emitir credenciais" }).click();
    await expect(page.getByText("não será exibido novamente")).toBeVisible();
    await expect(page.getByText(/client_secret:/)).toBeVisible();
    await page.getByRole("button", { name: "Já copiei" }).click();
    const row = page.getByRole("row").filter({ hasText: name });
    await expect(row).toContainText("Ativa");
    await row.getByRole("button", { name: "Revogar" }).click();
    await notice(page, /Integração revogada/);
    await expect(row).toContainText("Revogada");
    await expectAccessible(page);
  });

  await test.step("operação e auditoria", async () => {
    await openNav(page, "Operação");
    await page.getByRole("button", { name: "Reenfileirar eventos esgotados" }).click();
    await notice(page, /eventos esgotados voltaram para a fila/);
    await expectAccessible(page);
    await openNav(page, "Auditoria");
    await page.getByLabel(/^Ação/).fill("integration.revoked");
    await page.getByRole("button", { name: "Filtrar" }).click();
    await expect(
      page
        .getByRole("table", { name: "Eventos de auditoria" })
        .getByText("integration.revoked")
        .first(),
    ).toBeVisible();
    await expectAccessible(page);
  });
});

test("contrato: dados, acesso, anexo liberado pelo antivírus e valores", async ({ page }) => {
  let contractUrl = "";
  const title = `Manutenção predial ${RUN} (fictício)`;
  await login(page, "gestora.a@exemplo.test");

  await test.step("novo contrato", async () => {
    await page.goto("/contratos/novo");
    await page.getByLabel(/^Título/).fill(title);
    await page.getByLabel(/^Tipo/).selectOption("services");
    await page.getByLabel(/^Contraparte/).selectOption({ label: SEED_COUNTERPARTY });
    await page.getByLabel(/^Valor total/).fill("24000.00");
    await page.getByLabel(/^Texto inicial do rascunho/).fill("Cláusula 1. Objeto fictício.");
    await expectAccessible(page);
    await page.getByRole("button", { name: "Criar contrato" }).click();
    await notice(page, /Contrato .* criado\./);
    await expect(page).toHaveURL(/\/contratos\/[0-9a-f-]{36}\/rascunho$/);
    contractUrl = new URL(page.url()).pathname.replace(/\/rascunho$/, "");
  });

  await test.step("edição dos dados mantém o responsável", async () => {
    await page.goto(contractUrl);
    await page.getByLabel(/^Título/).fill(`${title} — revisado`);
    await page.getByRole("button", { name: "Salvar alterações" }).click();
    await notice(page, "Dados do contrato atualizados.");
    await expect(page.getByRole("heading", { level: 1 })).toContainText("revisado");
    await expect(page.locator(".definition-list")).toContainText("Gestora de contratos (fictícia)");
  });

  await test.step("acesso concedido e removido", async () => {
    await openTab(page, contractUrl, "Acesso");
    await page
      .getByLabel(/^Pessoa ou equipe/)
      .selectOption({ label: "Revisora (fictícia) — Organização A (fictícia)" });
    await page.getByRole("button", { name: "Conceder" }).click();
    await notice(page, "Acesso concedido.");
    const row = page.getByRole("row").filter({ hasText: "Revisora (fictícia)" });
    await expect(row).toContainText("Manual");
    await row.getByRole("button", { name: "Remover" }).click();
    await notice(page, "Acesso removido.");
    await expect(row).toHaveCount(0);
    await expectAccessible(page);
  });

  await test.step("anexo em quarentena, liberado pelo antivírus e anexado", async () => {
    await openTab(page, contractUrl, "Anexos");
    await page.getByLabel(/^Arquivo/).setInputFiles({
      name: `anexo-${RUN}.pdf`,
      mimeType: "application/pdf",
      buffer: samplePdf(`Anexo ficticio ${RUN}`),
    });
    const attach = page.getByRole("button", { name: "Anexar arquivo liberado" });
    await expect(attach).toBeEnabled({ timeout: 180_000 });
    await expect(page.locator(".badge").getByText("Liberado", { exact: true })).toBeVisible();
    await attach.click();
    await notice(page, "Arquivo anexado ao contrato.");
    const link = page.getByRole("link", { name: `anexo-${RUN}.pdf` });
    await expect(link).toBeVisible();
    const download = await page.request.get((await link.getAttribute("href")) ?? "");
    expect(download.status()).toBe(200);
    expect((await download.body()).subarray(0, 5).toString()).toBe("%PDF-");
    await expectAccessible(page);
  });

  await test.step("valores: parcelas, confirmação, índice e reajuste único", async () => {
    await page.getByRole("button", { name: "Sair" }).click();
    await login(page, "financeiro.a@exemplo.test");
    await openTab(page, contractUrl, "Valores");
    const schedule = page.getByRole("region", { name: "Gerar parcelas" });
    await schedule.getByLabel(/^Valor total/).fill("1000,00"); // vírgula decimal brasileira
    await schedule.getByLabel(/^Número de parcelas/).fill("3");
    await schedule.getByLabel(/^Primeiro vencimento/).fill(isoDate(30));
    await schedule.getByRole("button", { name: "Gerar parcelas" }).click();
    await notice(page, /3 parcelas criadas/);
    const installments = page.getByRole("table", { name: "Parcelas e marcos" });
    await expect(installments.getByText("R$ 333,34")).toBeVisible();

    await installments.getByRole("button", { name: "Confirmar pagamento" }).first().click();
    const dialog = page.getByRole("dialog", { name: "Confirmar parcela 1" });
    await dialog.getByLabel(/^Referência/).fill(`NF ${RUN}`);
    await dialog.getByRole("button", { name: "Confirmar" }).click();
    await notice(page, "Pagamento confirmado.");
    await expect(installments.getByText("Confirmada")).toBeVisible();

    const adjustments = page.getByRole("region", { name: "Reajustes" });
    const series = `Índice ${RUN} (fictício)`;
    await adjustments.getByLabel(/^Série/).fill(series);
    await adjustments.getByLabel(/^Fonte/).fill("Fonte fictícia");
    await adjustments.getByLabel(/^Período/).fill(isoDate(0));
    await adjustments.getByLabel(/^Variação/).fill("10");
    await adjustments.getByLabel(/^Nota de origem/).fill("Valor de demonstração");
    await adjustments.getByLabel("Valor demonstrativo (não oficial)").check();
    await adjustments.getByRole("button", { name: "Registrar valor" }).click();
    await notice(page, /registrado para o período/);
    const indexSelect = adjustments.getByLabel(/^Índice/);
    const option = await indexSelect.locator("option", { hasText: series }).getAttribute("value");
    await indexSelect.selectOption(option ?? "");
    await adjustments.getByLabel(/^Aplicar a partir de/).fill(isoDate(0));
    await adjustments.getByRole("button", { name: "Aplicar reajuste" }).click();
    await notice(page, "Reajuste aplicado às parcelas previstas.");
    await expect(installments.getByText("R$ 366,66")).toHaveCount(2);
    await adjustments.getByRole("button", { name: "Aplicar reajuste" }).click();
    await expect(adjustments.getByRole("alert")).toContainText("já reajustou");
    await expectAccessible(page);
  });
});

test("modelos, solicitações, contrapartes, calendário e relatórios", async ({ page }) => {
  await login(page, "gestora.a@exemplo.test");

  await test.step("modelo criado e publicado", async () => {
    await page.goto("/modelos/novo");
    await page.getByLabel(/^Nome/).fill(`Modelo ${RUN} (fictício)`);
    await page.getByLabel(/^Tipo de contrato/).selectOption("nda");
    await page.getByRole("button", { name: "Criar modelo" }).click();
    await notice(page, /Modelo criado/);
    await page.getByRole("button", { name: "Publicar rascunho como nova versão" }).click();
    await notice(page, "Versão 1 publicada.");
    await expect(
      page
        .getByRole("table", { name: "Versões publicadas" })
        .getByRole("cell", { name: "1", exact: true }),
    ).toBeVisible();
    await expectAccessible(page);
  });

  await test.step("solicitação recusada com motivo", async () => {
    await page.goto("/solicitacoes/nova");
    await page.getByLabel(/^Título/).fill(`Pedido ${RUN} (fictício)`);
    await page.getByLabel(/^Tipo de contrato/).selectOption("supply");
    await page.getByLabel(/^Objetivo/).fill("Compra de materiais (fictício).");
    await page.getByRole("button", { name: "Enviar solicitação" }).click();
    await notice(page, /Solicitação .* enviada\./);
    await page.getByRole("button", { name: "Recusar" }).click();
    const dialog = page.getByRole("dialog", { name: "Recusar solicitação" });
    await dialog.getByLabel(/^Motivo/).fill("Fora do orçamento (fictício).");
    await dialog.getByRole("button", { name: "Recusar" }).click();
    await notice(page, "Solicitação recusada.");
    await expect(page.locator(".page-header")).toContainText("Recusada");
    await expect(page.getByRole("button", { name: "Converter em contrato" })).toHaveCount(0);
  });

  await test.step("contraparte editada", async () => {
    await page.goto("/contrapartes");
    await page.getByRole("link", { name: SEED_COUNTERPARTY }).click();
    await page.getByLabel(/^Telefone/).fill(`(11) 4000-${RUN.slice(-4)}`);
    await page.getByRole("button", { name: "Salvar alterações" }).click();
    await notice(page, "Contraparte atualizada.");
    await expectAccessible(page);
  });

  await test.step("calendário e relatórios", async () => {
    await openNav(page, "Calendário");
    await page.getByLabel(/^De/).fill(isoDate(-30));
    await page.getByLabel(/^Até/).fill(isoDate(400));
    await page.getByRole("button", { name: "Mostrar período" }).click();
    await expect(page.getByRole("alert")).toContainText("Intervalo máximo de 400 dias.");
    await page.getByLabel(/^Até/).fill(isoDate(300));
    await page.getByRole("button", { name: "Mostrar período" }).click();
    await expect(page.getByText(/^De \d{2}\/\d{2}\/\d{4} a/)).toBeVisible();
    await expectAccessible(page);
    await openNav(page, "Relatórios");
    const csv = await page.request.get("/api/v1/reports/contracts.csv");
    expect(csv.status()).toBe(200);
    expect(csv.headers()["content-type"]).toContain("text/csv");
    const lines = (await csv.text()).split("\n").filter((line) => line.trim() !== "");
    expect(lines.length).toBeGreaterThanOrEqual(2);
    expect(lines.some((line) => line.includes("exemplo fictício A"))).toBe(true);
    await expectAccessible(page);
  });
});
