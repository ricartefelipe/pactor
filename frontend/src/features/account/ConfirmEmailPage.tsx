import { useMutation } from "@tanstack/react-query";
import { Link, useParams } from "react-router";

import * as auth from "../../api/auth";
import { FormError } from "../../components/forms";
import { Button } from "../../components/ui";
import { errorMessage } from "../../api/errors";
import { AuthLayout } from "./AuthLayout";

export function ConfirmEmailPage() {
  const { key = "" } = useParams();
  const confirm = useMutation({ mutationFn: () => auth.verifyEmail(key) });

  return (
    <AuthLayout title="Confirmar e-mail">
      {confirm.isSuccess ? (
        <>
          <p role="status">E-mail confirmado. Você já pode entrar.</p>
          <p className="auth__links">
            <Link to="/entrar">Entrar</Link>
          </p>
        </>
      ) : (
        <>
          <FormError message={confirm.isError ? errorMessage(confirm.error) : null} />
          <p>Confirme que este endereço de e-mail é seu.</p>
          <Button
            busy={confirm.isPending}
            onClick={() => {
              confirm.mutate();
            }}
          >
            Confirmar e-mail
          </Button>
        </>
      )}
    </AuthLayout>
  );
}
