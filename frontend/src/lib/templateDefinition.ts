/**
 * Leitura defensiva da definição de um modelo (JSON validado pelo servidor).
 * Usada só para montar o formulário de geração; o servidor revalida tudo.
 */
export type FieldType = "text" | "date" | "decimal" | "boolean" | "choice";

export interface TemplateField {
  key: string;
  label: string;
  type: FieldType;
  required: boolean;
  choices: readonly string[];
}

const TYPES: ReadonlySet<string> = new Set(["text", "date", "decimal", "boolean", "choice"]);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isFieldType(value: unknown): value is FieldType {
  return typeof value === "string" && TYPES.has(value);
}

export function templateFields(definition: unknown): TemplateField[] {
  if (!isRecord(definition) || !Array.isArray(definition.fields)) return [];
  return definition.fields.flatMap((raw): TemplateField[] => {
    if (!isRecord(raw)) return [];
    const { key, label, type, required, choices } = raw;
    if (typeof key !== "string" || typeof label !== "string" || !isFieldType(type)) return [];
    return [
      {
        key,
        label,
        type,
        required: required === true,
        choices: Array.isArray(choices)
          ? choices.filter((c): c is string => typeof c === "string")
          : [],
      },
    ];
  });
}

/** Valores do formulário no formato esperado pela API (decimal e data como texto). */
export function generationValues(
  fields: readonly TemplateField[],
  form: FormData,
): Record<string, string | boolean> {
  const values: Record<string, string | boolean> = {};
  for (const field of fields) {
    if (field.type === "boolean") {
      values[field.key] = form.get(field.key) === "on";
      continue;
    }
    const raw = form.get(field.key);
    if (typeof raw === "string" && raw.trim() !== "") values[field.key] = raw.trim();
  }
  return values;
}

export const EXAMPLE_DEFINITION = {
  fields: [
    { key: "contratante", label: "Contratante", type: "text", required: true },
    { key: "valor_mensal", label: "Valor mensal (R$)", type: "decimal", required: true },
    { key: "inicio", label: "Início", type: "date", required: true },
    { key: "sigilo", label: "Incluir confidencialidade", type: "boolean" },
  ],
  sections: [
    { key: "objeto", title: "Objeto", body: "{{contratante}} contrata os serviços descritos." },
    {
      key: "valor",
      title: "Valor",
      body: "Valor mensal de R$ {{valor_mensal}} a partir de {{inicio}}.",
    },
    {
      key: "sigilo",
      title: "Confidencialidade",
      body: "As partes manterão sigilo.",
      optional_when: "sigilo",
    },
  ],
} as const;
