import { describe, expect, it } from "vitest";

import { ApiError, errorMessage, networkError, toApiError } from "./errors";

describe("toApiError", () => {
  it("lê o formato de erro do Pactor com mensagens por campo", () => {
    const error = toApiError(400, {
      error: {
        code: "validation_failed",
        message: "Dados inválidos.",
        details: {
          title: ["Este campo é obrigatório."],
          signers: [{ email: ["E-mail inválido."] }],
        },
        request_id: "abc123",
      },
    });
    expect(error).toBeInstanceOf(ApiError);
    expect(error.code).toBe("validation_failed");
    expect(error.fieldErrors).toEqual({
      title: "Este campo é obrigatório.",
      signers: "E-mail inválido.",
    });
    expect(error.requestId).toBe("abc123");
  });

  it("não trata detalhes de erros de domínio como erros de campo", () => {
    const error = toApiError(409, {
      error: { code: "stale_version", message: "Mudou.", details: { current_version: 3 } },
    });
    expect(error.code).toBe("stale_version");
    expect(error.fieldErrors).toEqual({});
    expect(error.details).toEqual({ current_version: 3 });
  });

  it("lê o formato de erro do allauth", () => {
    const error = toApiError(400, {
      status: 400,
      errors: [{ message: "Senha incorreta.", code: "incorrect", param: "password" }],
    });
    expect(error.message).toBe("Senha incorreta.");
    expect(error.fieldErrors).toEqual({ password: "Senha incorreta." });
  });

  it("usa mensagens por situação quando o corpo não é reconhecido", () => {
    expect(toApiError(403, null).message).toBe("Você não tem permissão para esta operação.");
    expect(toApiError(502, "<html>").code).toBe("server_error");
    expect(toApiError(418, undefined).message).toBe("Não foi possível concluir a operação.");
  });

  it("expõe mensagens amigáveis para rede e erros desconhecidos", () => {
    expect(networkError().status).toBe(0);
    expect(errorMessage(new TypeError("x"))).toBe("Erro inesperado. Tente novamente.");
  });
});
