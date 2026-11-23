/**
 * Erros da API em um formato único.
 *
 * A API do Pactor responde `{"error": {code, message, details, request_id}}`;
 * o allauth headless responde `{"errors": [{message, code, param}]}`. Os dois
 * viram {@link ApiError}, com mensagens por campo quando houver.
 */

export type FieldErrors = Readonly<Record<string, string>>;

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly fieldErrors: FieldErrors;
  readonly requestId: string | null;
  readonly details: unknown;

  constructor(
    status: number,
    code: string,
    message: string,
    options: { fieldErrors?: FieldErrors; requestId?: string | null; details?: unknown } = {},
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.fieldErrors = options.fieldErrors ?? {};
    this.requestId = options.requestId ?? null;
    this.details = options.details;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function firstMessage(value: unknown): string | null {
  if (typeof value === "string") return value;
  if (Array.isArray(value)) {
    for (const item of value) {
      const message = firstMessage(item);
      if (message !== null) return message;
    }
  }
  if (isRecord(value)) {
    for (const item of Object.values(value)) {
      const message = firstMessage(item);
      if (message !== null) return message;
    }
  }
  return null;
}

/** Mensagens de validação do DRF (`{"campo": ["mensagem"]}`) achatadas por campo. */
function fieldErrorsFrom(details: unknown): FieldErrors {
  if (!isRecord(details)) return {};
  const errors: Record<string, string> = {};
  for (const [field, value] of Object.entries(details)) {
    const message = firstMessage(value);
    if (message !== null) errors[field] = message;
  }
  return errors;
}

const STATUS_MESSAGES: Readonly<Record<number, string>> = {
  401: "Sua sessão terminou. Entre novamente.",
  403: "Você não tem permissão para esta operação.",
  404: "Não encontrado.",
  409: "A operação conflita com o estado atual. Atualize e tente de novo.",
  413: "Arquivo grande demais.",
  429: "Muitas tentativas. Aguarde um pouco e tente de novo.",
};

export function toApiError(status: number, body: unknown): ApiError {
  if (isRecord(body) && isRecord(body.error)) {
    const { code, message, details, request_id: requestId } = body.error;
    const fieldErrors = code === "validation_failed" ? fieldErrorsFrom(details) : {};
    return new ApiError(
      status,
      typeof code === "string" ? code : "error",
      typeof message === "string" ? message : "Não foi possível concluir a operação.",
      {
        fieldErrors,
        requestId: typeof requestId === "string" ? requestId : null,
        details,
      },
    );
  }
  if (isRecord(body) && Array.isArray(body.errors)) {
    const fieldErrors: Record<string, string> = {};
    let message: string | null = null;
    for (const item of body.errors) {
      if (!isRecord(item) || typeof item.message !== "string") continue;
      message ??= item.message;
      if (typeof item.param === "string") fieldErrors[item.param] ??= item.message;
    }
    return new ApiError(status, "auth_error", message ?? "Não foi possível autenticar.", {
      fieldErrors,
    });
  }
  return new ApiError(
    status,
    status >= 500 ? "server_error" : "error",
    STATUS_MESSAGES[status] ??
      (status >= 500
        ? "O servidor não conseguiu concluir a operação. Tente novamente."
        : "Não foi possível concluir a operação."),
  );
}

export function networkError(): ApiError {
  return new ApiError(0, "network_error", "Sem conexão com o servidor. Verifique a rede.");
}

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  return "Erro inesperado. Tente novamente.";
}
