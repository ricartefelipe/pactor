/** Destino seguro após o login: somente caminhos internos (evita redirecionamento aberto). */
export function safeNext(raw: string | null): string {
  if (raw === null || !raw.startsWith("/") || raw.startsWith("//") || raw.startsWith("/\\")) {
    return "/";
  }
  return raw;
}
