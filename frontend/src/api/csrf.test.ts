import { afterEach, describe, expect, it } from "vitest";

import { csrfToken, withCsrf } from "./csrf";

afterEach(() => {
  document.cookie = "pactor_csrftoken=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/";
});

describe("CSRF", () => {
  it("lê o token do cookie correto", () => {
    expect(csrfToken("outro=1; pactor_csrftoken=abc%3D; mais=2")).toBe("abc=");
    expect(csrfToken("outro=1")).toBeNull();
  });

  it("envia o token só em métodos que alteram estado", () => {
    document.cookie = "pactor_csrftoken=token123; path=/";
    expect(
      withCsrf(new Request("http://localhost/x", { method: "POST" })).headers.get("X-CSRFToken"),
    ).toBe("token123");
    expect(withCsrf(new Request("http://localhost/x")).headers.get("X-CSRFToken")).toBeNull();
  });
});
