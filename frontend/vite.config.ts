import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// Em produção, o Django serve os arquivos compilados em /static/ (whitenoise)
// e devolve index.html para as rotas da aplicação; tudo na mesma origem da API.
const backend = process.env.PACTOR_BACKEND_URL ?? "http://127.0.0.1:8000";

export default defineConfig(({ command }) => ({
  base: command === "build" ? "/static/" : "/",
  plugins: [react()],
  build: {
    outDir: "dist",
    emptyOutDir: true,
    sourcemap: false,
  },
  server: {
    proxy: {
      "/api": backend,
      "/_allauth": backend,
      "/webhooks": backend,
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    restoreMocks: true,
  },
}));
