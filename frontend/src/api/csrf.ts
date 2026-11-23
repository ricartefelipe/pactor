/** Token CSRF do Django, lido do cookie (não é HttpOnly por decisão de configuração). */
const CSRF_COOKIE = "pactor_csrftoken";

export function csrfToken(cookies: string = document.cookie): string | null {
  for (const part of cookies.split(";")) {
    const [name, ...rest] = part.trim().split("=");
    if (name === CSRF_COOKIE) return decodeURIComponent(rest.join("="));
  }
  return null;
}

/** URL absoluta na origem atual (o `Request` exige URL absoluta fora do navegador). */
export function sameOrigin(path: string): string {
  return new URL(path, window.location.origin).toString();
}

const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS", "TRACE"]);

export function withCsrf(request: Request): Request {
  if (SAFE_METHODS.has(request.method.toUpperCase())) return request;
  const token = csrfToken();
  if (token !== null) request.headers.set("X-CSRFToken", token);
  return request;
}
