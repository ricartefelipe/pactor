import { describe, expect, it } from "vitest";

import {
  addDaysIso,
  decimalText,
  formatBytes,
  formatDate,
  formatMoney,
  initials,
  todayIso,
} from "./format";
import { CONTRACT_TYPE, enumValue, labelOf, options } from "./labels";
import { safeNext } from "./navigation";
import { snippetParts } from "./snippet";
import { generationValues, templateFields } from "./templateDefinition";

describe("formatação", () => {
  it("monta as iniciais do avatar a partir do nome, ignorando separadores", () => {
    expect(initials("Gestora de contratos (fictícia) — Organização A", "g@x.test")).toBe("GD");
    expect(initials("ana", "a@x.test")).toBe("A");
    expect(initials("Élio Úrsula Costa", "e@x.test")).toBe("ÉÚ");
    expect(initials("", "joao@x.test")).toBe("J");
    expect(initials("— 42", "maria@x.test")).toBe("M");
  });

  it("formata datas civis sem deslocamento de fuso", () => {
    expect(formatDate("2027-01-01")).toBe("01/01/2027");
    expect(formatDate(null)).toBe("—");
  });

  it("formata valores decimais exatos em reais", () => {
    expect(formatMoney("36000.00").replace(/\s/g, " ")).toBe("R$ 36.000,00");
    expect(formatMoney(null)).toBe("—");
    expect(formatMoney("abc")).toBe("abc");
  });

  it("soma dias e formata tamanhos", () => {
    expect(addDaysIso("2028-02-28", 1)).toBe("2028-02-29");
    expect(addDaysIso("2027-12-31", 1)).toBe("2028-01-01");
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(2048)).toBe("2,0 KB");
    expect(todayIso(new Date(2027, 5, 9, 23, 30))).toBe("2027-06-09");
  });
});

describe("rótulos", () => {
  it("valida valores de enum contra os rótulos", () => {
    expect(enumValue(CONTRACT_TYPE, "nda")).toBe("nda");
    expect(enumValue(CONTRACT_TYPE, "toString")).toBeUndefined();
    expect(enumValue(CONTRACT_TYPE, undefined)).toBeUndefined();
    expect(labelOf(CONTRACT_TYPE, "services")).toBe("Prestação de serviços");
    expect(labelOf(CONTRACT_TYPE, "desconhecido")).toBe("desconhecido");
    expect(options(CONTRACT_TYPE)[0]).toEqual({
      value: "services",
      label: "Prestação de serviços",
    });
  });
});

describe("redirecionamento após login", () => {
  it.each([
    ["/contratos?x=1", "/contratos?x=1"],
    [null, "/"],
    ["https://exemplo.invalid", "/"],
    ["//exemplo.invalid", "/"],
    ["/\\exemplo.invalid", "/"],
  ])("safeNext(%s) = %s", (raw, expected) => {
    expect(safeNext(raw)).toBe(expected);
  });
});

describe("trechos da pesquisa", () => {
  it("separa os termos marcados sem interpretar HTML", () => {
    expect(snippetParts("a «manutenção» de <b>x</b» «y»")).toEqual([
      { text: "a ", match: false },
      { text: "manutenção", match: true },
      { text: " de <b>x</b» ", match: false },
      { text: "y", match: true },
    ]);
    expect(snippetParts("")).toEqual([]);
  });
});

describe("definição de modelo", () => {
  const definition = {
    fields: [
      { key: "nome", label: "Nome", type: "text", required: true },
      { key: "sigilo", label: "Sigilo", type: "boolean" },
      { key: "plano", label: "Plano", type: "choice", choices: ["a", 1, "b"] },
      { key: "ruim", label: "Ruim", type: "script" },
      "inválido",
    ],
  };

  it("lê somente campos válidos", () => {
    expect(templateFields(definition)).toEqual([
      { key: "nome", label: "Nome", type: "text", required: true, choices: [] },
      { key: "sigilo", label: "Sigilo", type: "boolean", required: false, choices: [] },
      { key: "plano", label: "Plano", type: "choice", required: false, choices: ["a", "b"] },
    ]);
    expect(templateFields(null)).toEqual([]);
  });

  it("monta os valores no formato da API", () => {
    const form = new FormData();
    form.set("nome", "  Empresa  ");
    form.set("plano", "");
    expect(generationValues(templateFields(definition), form)).toEqual({
      nome: "Empresa",
      sigilo: false,
    });
  });
});

describe("entrada decimal", () => {
  it.each([
    ["4,5", "4.5"],
    [" 1500,00 ", "1500.00"],
    ["1500.00", "1500.00"],
    ["1.500,00", "1.500,00"],
    ["-0,25", "-0.25"],
  ])("decimalText(%s) = %s", (raw, expected) => {
    expect(decimalText(raw)).toBe(expected);
  });
});
