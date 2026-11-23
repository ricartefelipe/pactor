import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { QrCode, qrPath } from "./QrCode";

const URI = "otpauth://totp/Pactor:gestora.a%40exemplo.test?secret=JBSWY3DPEHPK3PXP&issuer=Pactor";

function darkModules(path: string): Set<string> {
  return new Set([...path.matchAll(/M(\d+) (\d+)/g)].map((m) => `${m[1] ?? ""},${m[2] ?? ""}`));
}

describe("QrCode", () => {
  it("gera a matriz com zona de silêncio e os padrões de localização nos cantos", () => {
    const { size, path } = qrPath(URI);
    const dark = darkModules(path);
    const modules = size - 8;
    expect(modules).toBeGreaterThanOrEqual(21);
    expect((modules - 17) % 4).toBe(0);
    // Padrão de localização 7×7: borda escura, anel claro e centro 3×3 escuro.
    for (const [x0, y0] of [
      [4, 4],
      [4 + modules - 7, 4],
      [4, 4 + modules - 7],
    ] as const) {
      expect(dark.has(`${x0},${y0}`)).toBe(true);
      expect(dark.has(`${x0 + 6},${y0 + 6}`)).toBe(true);
      expect(dark.has(`${x0 + 1},${y0 + 1}`)).toBe(false);
      expect(dark.has(`${x0 + 3},${y0 + 3}`)).toBe(true);
    }
    expect([...dark].every((cell) => cell.split(",").every((n) => Number(n) >= 4))).toBe(true);
    expect(qrPath(URI)).toEqual({ size, path });
  });

  it("é uma imagem com descrição acessível", () => {
    render(<QrCode text={URI} label="QR code do autenticador" />);
    const image = screen.getByRole("img", { name: "QR code do autenticador" });
    expect(image.tagName.toLowerCase()).toBe("svg");
    expect(image.querySelector("path")?.getAttribute("d")).toMatch(/^M\d+ \d+h1v1h-1z/);
  });
});
