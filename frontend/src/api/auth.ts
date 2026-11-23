/**
 * Cliente do allauth headless (cliente "browser", sessão por cookie).
 *
 * Respostas 401 do allauth não são falhas: descrevem o próximo passo do fluxo
 * (ex.: segundo fator pendente). Erros de validação (400) viram {@link ApiError}.
 */
import { sameOrigin, withCsrf } from "./csrf";
import { ApiError, networkError, toApiError } from "./errors";

const BASE = "/_allauth/browser/v1";

export type AuthStep = "authenticated" | "anonymous" | "mfa_pending" | "verification_pending";

interface Flow {
  id: string;
  is_pending?: boolean;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

async function request(
  method: "GET" | "POST" | "DELETE",
  path: string,
  body?: Record<string, string>,
): Promise<{ status: number; payload: unknown }> {
  const init: RequestInit = { method, credentials: "same-origin", headers: {} };
  if (body !== undefined) {
    init.body = JSON.stringify(body);
    init.headers = { "Content-Type": "application/json" };
  }
  let response: Response;
  try {
    response = await fetch(withCsrf(new Request(sameOrigin(`${BASE}${path}`), init)));
  } catch {
    throw networkError();
  }
  const payload: unknown = response.headers.get("Content-Type")?.includes("json")
    ? await response.json()
    : null;
  return { status: response.status, payload };
}

function pendingFlows(payload: unknown): Flow[] {
  if (!isRecord(payload) || !isRecord(payload.data) || !Array.isArray(payload.data.flows)) {
    return [];
  }
  return payload.data.flows.filter(
    (flow): flow is Flow =>
      isRecord(flow) && typeof flow.id === "string" && flow.is_pending === true,
  );
}

/** Interpreta a resposta de qualquer passo de autenticação. */
function step(status: number, payload: unknown): AuthStep {
  if (status === 200) return "authenticated";
  if (status === 401) {
    const pending = pendingFlows(payload).map((flow) => flow.id);
    if (pending.includes("mfa_authenticate")) return "mfa_pending";
    if (pending.includes("verify_email")) return "verification_pending";
    return "anonymous";
  }
  if (status === 410) return "anonymous";
  throw toApiError(status, payload);
}

export async function session(): Promise<AuthStep> {
  const { status, payload } = await request("GET", "/auth/session");
  return step(status, payload);
}

export async function login(email: string, password: string): Promise<AuthStep> {
  const { status, payload } = await request("POST", "/auth/login", { email, password });
  return step(status, payload);
}

export async function authenticateSecondFactor(code: string): Promise<AuthStep> {
  const { status, payload } = await request("POST", "/auth/2fa/authenticate", { code });
  return step(status, payload);
}

/** Registra o segundo fator na sessão atual (exigido por ações privilegiadas). */
export async function reauthenticateSecondFactor(code: string): Promise<void> {
  const { status, payload } = await request("POST", "/auth/2fa/reauthenticate", { code });
  if (status !== 200) throw toApiError(status, payload);
}

export async function logout(): Promise<void> {
  const { status, payload } = await request("DELETE", "/auth/session");
  // O allauth responde 401 quando a sessão foi encerrada.
  if (status !== 401 && status !== 200) throw toApiError(status, payload);
}

export async function signup(email: string, password: string): Promise<AuthStep> {
  const { status, payload } = await request("POST", "/auth/signup", { email, password });
  return step(status, payload);
}

export async function verifyEmail(key: string): Promise<AuthStep> {
  const { status, payload } = await request("POST", "/auth/email/verify", { key });
  return step(status, payload);
}

export async function requestPasswordReset(email: string): Promise<void> {
  const { status, payload } = await request("POST", "/auth/password/request", { email });
  if (status !== 200) throw toApiError(status, payload);
}

export async function resetPassword(key: string, password: string): Promise<AuthStep> {
  const { status, payload } = await request("POST", "/auth/password/reset", { key, password });
  return step(status, payload);
}

export interface TotpSetup {
  active: boolean;
  secret: string | null;
  uri: string | null;
}

export async function totpStatus(): Promise<TotpSetup> {
  const { status, payload } = await request("GET", "/account/authenticators/totp");
  if (status === 200) return { active: true, secret: null, uri: null };
  if (status === 404 && isRecord(payload) && isRecord(payload.meta)) {
    const { secret, totp_url: uri } = payload.meta;
    if (typeof secret === "string" && typeof uri === "string") {
      return { active: false, secret, uri };
    }
  }
  throw toApiError(status, payload);
}

export async function activateTotp(code: string): Promise<void> {
  const { status, payload } = await request("POST", "/account/authenticators/totp", { code });
  if (status !== 200) throw toApiError(status, payload);
}

export { ApiError };
