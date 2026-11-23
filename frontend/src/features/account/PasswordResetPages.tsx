import { useMutation } from "@tanstack/react-query";
import { useState, type SubmitEvent } from "react";
import { Link, useParams } from "react-router";

import * as auth from "../../api/auth";
import { ApiError, errorMessage } from "../../api/errors";
import { FormError, TextField, textOf } from "../../components/forms";
import { Button } from "../../components/ui";
import { AuthLayout } from "./AuthLayout";

export function PasswordResetRequestPage() {
  const request = useMutation({ mutationFn: auth.requestPasswordReset });

  function handleSubmit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    request.mutate(textOf(event.currentTarget, "email"));
  }

  return (
    <AuthLayout title="Redefinir senha">
      {request.isSuccess ? (
        // Mesma resposta para e-mails cadastrados ou não (sem enumeração de contas).
        <p role="status">
          Se houver uma conta com esse e-mail, você receberá um link para criar uma nova senha.
        </p>
      ) : (
        <form onSubmit={handleSubmit} noValidate>
          <FormError message={request.isError ? errorMessage(request.error) : null} />
          <TextField label="E-mail" name="email" type="email" autoComplete="email" required />
          <Button type="submit" busy={request.isPending}>
            Enviar link
          </Button>
        </form>
      )}
      <p className="auth__links">
        <Link to="/entrar">Voltar ao login</Link>
      </p>
    </AuthLayout>
  );
}

export function PasswordResetPage() {
  const { key = "" } = useParams();
  const [mismatch, setMismatch] = useState(false);
  const reset = useMutation({
    mutationFn: (password: string) => auth.resetPassword(key, password),
  });
  const fieldErrors = reset.error instanceof ApiError ? reset.error.fieldErrors : {};

  function handleSubmit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const password = textOf(form, "password");
    const matches = password === textOf(form, "confirmation");
    setMismatch(!matches);
    if (matches) reset.mutate(password);
  }

  return (
    <AuthLayout title="Nova senha">
      {reset.isSuccess ? (
        <>
          <p role="status">Senha alterada. Entre com a nova senha.</p>
          <p className="auth__links">
            <Link to="/entrar">Entrar</Link>
          </p>
        </>
      ) : (
        <form onSubmit={handleSubmit} noValidate>
          <FormError message={reset.isError ? errorMessage(reset.error) : null} />
          <TextField
            label="Nova senha"
            name="password"
            type="password"
            autoComplete="new-password"
            required
            error={fieldErrors.password}
          />
          <TextField
            label="Confirme a nova senha"
            name="confirmation"
            type="password"
            autoComplete="new-password"
            required
            error={mismatch ? "As senhas não coincidem." : undefined}
          />
          <Button type="submit" busy={reset.isPending}>
            Salvar nova senha
          </Button>
        </form>
      )}
    </AuthLayout>
  );
}
