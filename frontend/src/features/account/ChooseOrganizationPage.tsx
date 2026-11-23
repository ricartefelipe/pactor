import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState, type SubmitEvent } from "react";

import { api, unwrap } from "../../api/client";
import { ApiError, errorMessage } from "../../api/errors";
import { ME_KEY, discardCachedData, useSession } from "../../auth/session";
import { FormError, TextField, textOf } from "../../components/forms";
import { Button } from "../../components/ui";
import { AuthLayout } from "./AuthLayout";

export function ChooseOrganizationPage() {
  const { me, switchOrganization, signOut } = useSession();
  const queryClient = useQueryClient();
  const [choosing, setChoosing] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const create = useMutation({
    mutationFn: (name: string) => unwrap(api.POST("/api/v1/organizations", { body: { name } })),
    onSuccess: (updated) => {
      discardCachedData(queryClient);
      queryClient.setQueryData(ME_KEY, updated);
    },
  });

  async function choose(organizationId: string) {
    setChoosing(organizationId);
    setError(null);
    try {
      await switchOrganization(organizationId);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setChoosing(null);
    }
  }

  function handleCreate(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    create.mutate(textOf(event.currentTarget, "name"));
  }

  return (
    <AuthLayout title="Escolha a organização">
      <FormError message={error} />
      {me.organizations.length > 0 ? (
        <ul className="choice-list">
          {me.organizations.map((organization) => (
            <li key={organization.id}>
              <Button
                variant="secondary"
                busy={choosing === organization.id}
                onClick={() => void choose(organization.id)}
              >
                {organization.name}
              </Button>
            </li>
          ))}
        </ul>
      ) : (
        <p>Você ainda não participa de nenhuma organização. Aceite um convite ou crie uma.</p>
      )}
      <form onSubmit={handleCreate} noValidate className="form">
        <h2>Criar organização</h2>
        <FormError message={create.isError ? errorMessage(create.error) : null} />
        <TextField
          label="Nome da organização"
          name="name"
          required
          maxLength={200}
          error={create.error instanceof ApiError ? create.error.fieldErrors.name : undefined}
        />
        <Button type="submit" busy={create.isPending}>
          Criar e começar o período de avaliação
        </Button>
      </form>
      <p className="auth__links">
        <button type="button" className="button button--link" onClick={() => void signOut()}>
          Sair
        </button>
      </p>
    </AuthLayout>
  );
}
