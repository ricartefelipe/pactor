import { useCallback, useState } from "react";

import { newIdempotencyKey } from "../api/client";
import { ApiError } from "../api/errors";

/**
 * Uma chave por intenção do usuário. Em falha de rede ou do servidor a mesma
 * chave é reenviada (a repetição devolve o resultado original); após uma
 * recusa definitiva (4xx) nada foi gravado e uma nova chave é gerada.
 */
export function useIdempotencyKey(): { key: string; settle: (error?: unknown) => void } {
  const [key, setKey] = useState(newIdempotencyKey);
  const settle = useCallback((error?: unknown) => {
    const definitive =
      error === undefined ||
      (error instanceof ApiError && error.status >= 400 && error.status < 500);
    if (definitive) setKey(newIdempotencyKey());
  }, []);
  return { key, settle };
}
