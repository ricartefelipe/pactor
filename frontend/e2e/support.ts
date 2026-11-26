import { createHmac } from "node:crypto";

import AxeBuilder from "@axe-core/playwright";
import { expect, type Page } from "@playwright/test";

/** Credenciais públicas do seed de demonstração (docs/DEMONSTRACAO.md). */
export const DEMO_PASSWORD = "Pactor-Demo-2026!";
const DEMO_TOTP_SECRET = "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP";
const MAILPIT = process.env.PACTOR_E2E_MAILPIT ?? "http://localhost:8025";

function base32(secret: string): Buffer {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  let bits = "";
  for (const char of secret.replace(/=+$/, "")) {
    const index = alphabet.indexOf(char);
    if (index < 0) throw new Error("segredo base32 inválido");
    bits += index.toString(2).padStart(5, "0");
  }
  const bytes: number[] = [];
  for (let i = 0; i + 8 <= bits.length; i += 8)
    bytes.push(Number.parseInt(bits.slice(i, i + 8), 2));
  return Buffer.from(bytes);
}

/** Código TOTP (RFC 6238, SHA-1, 30 s, 6 dígitos) do segredo de demonstração. */
export function totp(now: number = Date.now()): string {
  const counter = Buffer.alloc(8);
  counter.writeBigUInt64BE(BigInt(Math.floor(now / 1000 / 30)));
  const digest = createHmac("sha1", base32(DEMO_TOTP_SECRET)).update(counter).digest();
  const offset = (digest[digest.length - 1] ?? 0) & 0x0f;
  const value = (digest.readUInt32BE(offset) & 0x7fffffff) % 1_000_000;
  return value.toString().padStart(6, "0");
}

/** CNPJ fictício com dígitos verificadores válidos. */
export function cnpj(seed: number): string {
  const base = `${String(seed % 100_000_000).padStart(8, "0")}0001`;
  const digit = (digits: string, weights: number[]) => {
    let sum = 0;
    for (let i = 0; i < digits.length; i += 1) sum += Number(digits.charAt(i)) * (weights[i] ?? 0);
    const rest = sum % 11;
    return rest < 2 ? 0 : 11 - rest;
  };
  const first = digit(base, [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]);
  const second = digit(`${base}${first}`, [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]);
  return `${base}${first}${second}`;
}

const TOTP_PERIOD_MS = 30_000;
let lastTotpWindow = -1;

/**
 * Código de uma janela TOTP ainda não usada nesta execução: o servidor recusa
 * reutilizar um código, e testes seguidos podem entrar na mesma janela.
 */
async function unusedTotp(page: Page): Promise<string> {
  let window = Math.floor(Date.now() / TOTP_PERIOD_MS);
  if (window <= lastTotpWindow) {
    await page.waitForTimeout((lastTotpWindow + 1) * TOTP_PERIOD_MS - Date.now() + 500);
    window = Math.floor(Date.now() / TOTP_PERIOD_MS);
  }
  lastTotpWindow = window;
  return totp(window * TOTP_PERIOD_MS);
}

async function confirmSecondFactor(page: Page): Promise<void> {
  const field = page.getByLabel("Código do aplicativo autenticador");
  await field.fill(await unusedTotp(page));
  await page.getByRole("button", { name: "Confirmar código" }).click();
  // Outro processo pode ter usado o código desta janela: tenta uma vez na seguinte.
  const accepted = page.getByRole("heading", { level: 1, name: "Painel" });
  const rejected = page.getByText("Código incorreto.").first();
  await expect(accepted.or(rejected)).toBeVisible();
  if (await rejected.isVisible()) {
    await field.fill(await unusedTotp(page));
    await page.getByRole("button", { name: "Confirmar código" }).click();
  }
}

export async function login(page: Page, email: string, { mfa = false } = {}): Promise<void> {
  await page.goto("/entrar");
  await page.getByLabel("E-mail").fill(email);
  await page.getByLabel("Senha").fill(DEMO_PASSWORD);
  await page.getByRole("button", { name: "Entrar" }).click();
  if (mfa) {
    await confirmSecondFactor(page);
  }
  await expect(page.getByRole("heading", { level: 1, name: "Painel" })).toBeVisible();
}

export async function logout(page: Page): Promise<void> {
  await page.getByRole("button", { name: "Sair" }).click();
  await expect(page.getByRole("heading", { name: "Entrar" })).toBeVisible();
}

/** Nenhuma violação grave ou crítica das regras WCAG 2 A/AA na página atual. */
export async function expectAccessible(page: Page): Promise<void> {
  const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
  const serious = results.violations.filter(
    (v) => v.impact === "serious" || v.impact === "critical",
  );
  expect(serious.map((v) => `${v.id}: ${v.help}`)).toEqual([]);
}

interface MailpitSearch {
  messages: { Subject: string; To: { Address: string }[] }[];
}

export async function mailsMatching(text: string): Promise<MailpitSearch["messages"]> {
  const response = await fetch(`${MAILPIT}/api/v1/search?query=${encodeURIComponent(text)}`);
  if (!response.ok) throw new Error(`Mailpit respondeu ${String(response.status)}`);
  const body = (await response.json()) as MailpitSearch;
  return body.messages;
}

/** Fuso das organizações da carga de demonstração, o mesmo do navegador (playwright.config). */
const ORGANIZATION_TIME_ZONE = "America/Sao_Paulo";

/**
 * Data civil (AAAA-MM-DD) daqui a `offsetDays` dias no fuso da organização. O processo
 * dos testes pode rodar em outro fuso (UTC na CI): à noite, o "hoje" dele já é amanhã
 * para o servidor, e um contrato com início "hoje" ficaria aguardando a vigência.
 */
export function isoDate(offsetDays: number): string {
  const date = new Date(Date.now() + offsetDays * 86_400_000);
  // en-CA formata datas como AAAA-MM-DD.
  return new Intl.DateTimeFormat("en-CA", { timeZone: ORGANIZATION_TIME_ZONE }).format(date);
}

/** PDF mínimo e válido (uma página com texto ASCII), com tabela xref correta. */
export function samplePdf(text: string): Buffer {
  const content = `BT /F1 12 Tf 72 720 Td (${text.replace(/[()\\]/g, "")}) Tj ET`;
  const objects = [
    "<< /Type /Catalog /Pages 2 0 R >>",
    "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
    "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
    `<< /Length ${String(content.length)} >>\nstream\n${content}\nendstream`,
    "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
  ];
  let body = "%PDF-1.4\n";
  const offsets: number[] = [];
  objects.forEach((object, index) => {
    offsets.push(body.length);
    body += `${String(index + 1)} 0 obj\n${object}\nendobj\n`;
  });
  const xref = body.length;
  body += `xref\n0 ${String(objects.length + 1)}\n0000000000 65535 f \n`;
  for (const offset of offsets) body += `${String(offset).padStart(10, "0")} 00000 n \n`;
  body += `trailer\n<< /Size ${String(objects.length + 1)} /Root 1 0 R >>\nstartxref\n${String(xref)}\n%%EOF\n`;
  return Buffer.from(body, "latin1");
}
