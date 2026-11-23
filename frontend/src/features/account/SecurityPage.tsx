import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { SubmitEvent } from "react";
import { Link, useSearchParams } from "react-router";

import * as auth from "../../api/auth";
import { ApiError, errorMessage } from "../../api/errors";
import { ME_KEY, useSession } from "../../auth/session";
import { FormError, TextField, textOf } from "../../components/forms";
import { useNotify } from "../../components/notifications";
import { QrCode } from "../../components/QrCode";
import { QueryView } from "../../components/states";
import { Button, PageHeader, Section } from "../../components/ui";
import { safeNext } from "../../lib/navigation";

function CodeForm({
  label,
  submitLabel,
  onCode,
  pending,
  error,
}: {
  label: string;
  submitLabel: string;
  onCode: (code: string) => void;
  pending: boolean;
  error: unknown;
}) {
  function handleSubmit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    onCode(textOf(event.currentTarget, "code"));
  }
  const fieldErrors = error instanceof ApiError ? error.fieldErrors : {};
  return (
    <form onSubmit={handleSubmit} noValidate className="form form--inline">
      <FormError message={error ? errorMessage(error) : null} />
      <TextField
        label={label}
        name="code"
        inputMode="numeric"
        autoComplete="one-time-code"
        pattern="[0-9]{6}"
        maxLength={6}
        required
        error={fieldErrors.code}
      />
      <Button type="submit" busy={pending}>
        {submitLabel}
      </Button>
    </form>
  );
}

export function SecurityPage() {
  const { me } = useSession();
  const [params] = useSearchParams();
  const next = params.get("proxima");
  const queryClient = useQueryClient();
  const notify = useNotify();
  const totp = useQuery({ queryKey: ["auth", "totp"], queryFn: auth.totpStatus });

  const refresh = async () => {
    await queryClient.invalidateQueries({ queryKey: ME_KEY });
    await queryClient.invalidateQueries({ queryKey: ["auth", "totp"] });
  };
  const activate = useMutation({
    mutationFn: auth.activateTotp,
    onSuccess: async () => {
      notify.success("Verificação em duas etapas ativada.");
      await refresh();
    },
  });
  const confirm = useMutation({
    mutationFn: auth.reauthenticateSecondFactor,
    onSuccess: async () => {
      notify.success("Código confirmado nesta sessão.");
      await refresh();
    },
  });

  return (
    <>
      <PageHeader title="Segurança" description={me.email} />
      <Section title="Verificação em duas etapas">
        <QueryView query={totp}>
          {(status) =>
            status.active ? (
              <>
                <p>Ativa. Ações administrativas pedem o código do aplicativo autenticador.</p>
                {me.mfa_satisfied ? (
                  <p role="status">O segundo fator já foi confirmado nesta sessão.</p>
                ) : (
                  <CodeForm
                    label="Código para confirmar nesta sessão"
                    submitLabel="Confirmar código"
                    onCode={(code) => {
                      confirm.mutate(code);
                    }}
                    pending={confirm.isPending}
                    error={confirm.error}
                  />
                )}
              </>
            ) : (
              <>
                <p>
                  Leia o QR code com um aplicativo autenticador (ou digite a chave) e informe o
                  código gerado.
                </p>
                {status.uri && (
                  <QrCode
                    text={status.uri}
                    label="QR code para cadastrar o Pactor no aplicativo autenticador"
                  />
                )}
                <p>
                  Chave: <code className="secret">{status.secret}</code>
                </p>
                <details>
                  <summary>Endereço de configuração (otpauth)</summary>
                  <code className="secret">{status.uri}</code>
                </details>
                <CodeForm
                  label="Código do aplicativo"
                  submitLabel="Ativar"
                  onCode={(code) => {
                    activate.mutate(code);
                  }}
                  pending={activate.isPending}
                  error={activate.error}
                />
              </>
            )
          }
        </QueryView>
      </Section>
      {next !== null && me.mfa_satisfied && (
        <p>
          <Link to={safeNext(next)}>Voltar para onde você estava</Link>
        </p>
      )}
    </>
  );
}
