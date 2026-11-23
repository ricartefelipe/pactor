import { defineConfig, devices } from "@playwright/test";

/**
 * Roteiro demonstrativo contra a pilha completa (web, workers, agendador,
 * PostgreSQL, RabbitMQ, Redis, Mailpit e ClamAV) com o seed de demonstração.
 */
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  forbidOnly: Boolean(process.env.CI),
  retries: 0,
  timeout: 8 * 60_000,
  expect: { timeout: 15_000 },
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: process.env.PACTOR_E2E_URL ?? "http://localhost:8000",
    locale: "pt-BR",
    timezoneId: "America/Sao_Paulo",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
