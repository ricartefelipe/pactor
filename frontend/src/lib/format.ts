/** Formatação pt-BR. Datas da API (AAAA-MM-DD) são datas civis, sem fuso. */

const dateFormat = new Intl.DateTimeFormat("pt-BR", { timeZone: "UTC" });
const dateTimeFormat = new Intl.DateTimeFormat("pt-BR", {
  dateStyle: "short",
  timeStyle: "short",
});
const moneyFormat = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" });

export function formatDate(value: string | null | undefined): string {
  if (!value) return "—";
  const [year, month, day] = value.slice(0, 10).split("-").map(Number);
  if (year === undefined || month === undefined || day === undefined) return value;
  return dateFormat.format(new Date(Date.UTC(year, month - 1, day)));
}

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return "—";
  return dateTimeFormat.format(new Date(value));
}

/**
 * Valores monetários chegam como texto decimal exato ("1500.00"). A conversão
 * para número acontece só na exibição; cálculos ficam no servidor.
 */
export function formatMoney(value: string | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  const amount = Number(value);
  return Number.isFinite(amount) ? moneyFormat.format(amount) : value;
}

export function formatBytes(size: number): string {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1).replace(".", ",")} KB`;
  return `${(size / (1024 * 1024)).toFixed(1).replace(".", ",")} MB`;
}

export function todayIso(now: Date = new Date()): string {
  const local = new Date(now.getTime() - now.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 10);
}

export function addDaysIso(isoDate: string, days: number): string {
  const [year, month, day] = isoDate.split("-").map(Number);
  if (year === undefined || month === undefined || day === undefined) return isoDate;
  const date = new Date(Date.UTC(year, month - 1, day + days));
  return date.toISOString().slice(0, 10);
}

/**
 * Texto decimal para a API: aceita a vírgula brasileira ("4,5" → "4.5"). Separador
 * de milhar não é interpretado ("1.500,00" segue como está e é recusado pela API),
 * pois "1.500" seria ambíguo.
 */
export function decimalText(raw: string): string {
  const text = raw.trim();
  return text.includes(",") && !text.includes(".") ? text.replace(",", ".") : text;
}

/** Iniciais para o avatar (duas primeiras palavras do nome, ou o e-mail). */
export function initials(name: string, email: string): string {
  const words = name
    .split(/[\s—-]+/)
    .filter((word) => /^\p{L}/u.test(word))
    .slice(0, 2);
  const letters = words.map((word) => word.charAt(0)).join("");
  return (letters || email.charAt(0)).toUpperCase();
}
