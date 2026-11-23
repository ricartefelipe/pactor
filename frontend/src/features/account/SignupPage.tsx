import { useState, type SubmitEvent } from "react";
import { Link } from "react-router";

import * as auth from "../../api/auth";
import { ApiError, errorMessage, type FieldErrors } from "../../api/errors";
import { FormError, TextField, textOf } from "../../components/forms";
import { Button } from "../../components/ui";
import { AuthLayout } from "./AuthLayout";

export function SignupPage() {
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});

  async function handleSubmit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const password = textOf(form, "password");
    if (password !== textOf(form, "confirmation")) {
      setFieldErrors({ confirmation: "As senhas não coincidem." });
      return;
    }
    setBusy(true);
    setError(null);
    setFieldErrors({});
    try {
      await auth.signup(textOf(form, "email"), password);
      setDone(true);
    } catch (caught) {
      setError(errorMessage(caught));
      if (caught instanceof ApiError) setFieldErrors(caught.fieldErrors);
    } finally {
      setBusy(false);
    }
  }

  if (done) {
    return (
      <AuthLayout title="Confirme seu e-mail">
        <p role="status">
          Enviamos um link de confirmação para o seu e-mail. Depois de confirmar, entre com sua
          senha.
        </p>
        <p className="auth__links">
          <Link to="/entrar">Ir para o login</Link>
        </p>
      </AuthLayout>
    );
  }

  return (
    <AuthLayout title="Criar conta">
      <form onSubmit={(event) => void handleSubmit(event)} noValidate>
        <FormError message={error} />
        <TextField
          label="E-mail"
          name="email"
          type="email"
          autoComplete="email"
          required
          error={fieldErrors.email}
        />
        <TextField
          label="Senha"
          name="password"
          type="password"
          autoComplete="new-password"
          required
          hint="Ao menos 12 caracteres, evitando senhas comuns."
          error={fieldErrors.password}
        />
        <TextField
          label="Confirme a senha"
          name="confirmation"
          type="password"
          autoComplete="new-password"
          required
          error={fieldErrors.confirmation}
        />
        <Button type="submit" busy={busy}>
          Criar conta
        </Button>
      </form>
      <p className="auth__links">
        Já tem conta? <Link to="/entrar">Entrar</Link>
      </p>
    </AuthLayout>
  );
}
