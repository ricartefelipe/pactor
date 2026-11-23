import createClient, { type Middleware } from "openapi-fetch";

import { withCsrf } from "./csrf";
import { ApiError, networkError, toApiError } from "./errors";
import type { components, paths } from "./schema";

export type Schemas = components["schemas"];

const csrf: Middleware = {
  onRequest({ request }) {
    return withCsrf(request);
  },
};

export const api = createClient<paths>({
  baseUrl: window.location.origin,
  credentials: "same-origin",
  // Resolve o fetch a cada chamada (permite substituí-lo em testes e extensões de rede).
  fetch: (request) => globalThis.fetch(request),
});
api.use(csrf);

interface Result<T> {
  data?: T;
  error?: unknown;
  response: Response;
}

async function settle<T>(pending: Promise<Result<T>>): Promise<Result<T>> {
  try {
    return await pending;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    throw networkError();
  }
}

/** Resolve uma chamada tipada: devolve os dados ou lança {@link ApiError}. */
export async function unwrap<T>(pending: Promise<Result<T>>): Promise<T> {
  const { data, error, response } = await settle(pending);
  if (!response.ok) throw toApiError(response.status, error);
  if (data === undefined) {
    throw new ApiError(response.status, "empty_response", "Resposta vazia inesperada do servidor.");
  }
  return data;
}

/** Para respostas sem corpo (ex.: 204). */
export async function unwrapEmpty(pending: Promise<Result<unknown>>): Promise<void> {
  const { error, response } = await settle(pending);
  if (!response.ok) throw toApiError(response.status, error);
}

/** Chave para operações idempotentes; uma por intenção do usuário, não por tentativa. */
export function newIdempotencyKey(): string {
  return crypto.randomUUID();
}
