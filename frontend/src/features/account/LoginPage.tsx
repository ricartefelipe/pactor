import { useQueryClient } from "@tanstack/react-query";
import { useState, type SubmitEvent } from "react";
import { Link, Navigate, useNavigate, useSearchParams } from "react-router";

import * as auth from "../../api/auth";
import { ApiError, errorMessage, type FieldErrors } from "../../api/errors";
import { AUTH_KEY, discardCachedData, useAuthStep } from "../../auth/session";
import { FormError, TextField, textOf } from "../../components/forms";
import { Loading } from "../../components/states";
import { Button } from "../../components/ui";
import { safeNext } from "../../lib/navigation";
import { AuthLayout } from "./AuthLayout";

export function LoginPage() {
  const [params] = useSearchParams();
  const next = safeNext(params.get("proxima"));
  const session = useAuthStep();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [stage, setStage] = useState<"password" | "code">("password");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});

  async function finish(step: auth.AuthStep) {
    if (step === "authenticated") {
      // Nada de uma sessão anterior (outro usuário) pode sobreviver ao login.
      discardCachedData(queryClient, ["auth"]);
      queryClient.setQueryData(AUTH_KEY, "authenticated");
      await navigate(next, { replace: true });
    } else if (step === "mfa_pending") {
      setStage("code");
    } else if (step === "verification_pending") {
      setError("Confirme seu e-mail pelo link enviado antes de entrar.");
    } else {
      setError("Não foi possível entrar. Confira os dados e tente de novo.");
    }
  }

  async function handleSubmit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    setBusy(true);
    setError(null);
    setFieldErrors({});
    try {
      const step =
        stage === "password"
          ? await auth.login(textOf(form, "email"), textOf(form, "password"))
          : await auth.authenticateSecondFactor(textOf(form, "code"));
      await finish(step);
    } catch (caught) {
      setError(errorMessage(caught));
      if (caught instanceof ApiError) setFieldErrors(caught.fieldErrors);
    } finally {
      setBusy(false);
    }
  }

  if (session.isPending) return <Loading label="Verificando sessão…" />;
  if (session.data === "authenticated") return <Navigate to={next} replace />;

  return (
    <AuthLayout title={stage === "password" ? "Entrar" : "Verificação em duas etapas"}>
      <form onSubmit={(event) => void handleSubmit(event)} noValidate>
        <FormError message={error} />
        {stage === "password" ? (
          <>
            <TextField
              label="E-mail"
              name="email"
              type="email"
              autoComplete="username"
              required
              error={fieldErrors.email}
            />
            <TextField
              label="Senha"
              name="password"
              type="password"
              autoComplete="current-password"
              required
              error={fieldErrors.password}
            />
          </>
        ) : (
          <TextField
            label="Código do aplicativo autenticador"
            name="code"
            inputMode="numeric"
            autoComplete="one-time-code"
            pattern="[0-9]{6}"
            maxLength={6}
            required
            autoFocus
            hint="Digite os 6 dígitos exibidos no aplicativo."
            error={fieldErrors.code}
          />
        )}
        <Button type="submit" busy={busy}>
          {stage === "password" ? "Entrar" : "Confirmar código"}
        </Button>
      </form>
      {stage === "password" && (
        <p className="auth__links">
          <Link to="/conta/redefinir-senha">Esqueci minha senha</Link> ·{" "}
          <Link to="/cadastro">Criar conta</Link>
        </p>
      )}
    </AuthLayout>
  );
}
