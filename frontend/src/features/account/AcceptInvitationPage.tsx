import { useMutation } from "@tanstack/react-query";
import { useNavigate, useSearchParams } from "react-router";

import { api, unwrap } from "../../api/client";
import { errorMessage } from "../../api/errors";
import { useSession } from "../../auth/session";
import { FormError } from "../../components/forms";
import { useNotify } from "../../components/notifications";
import { Button } from "../../components/ui";
import { AuthLayout } from "./AuthLayout";

export function AcceptInvitationPage() {
  const [params] = useSearchParams();
  const token = params.get("token") ?? "";
  const { me, switchOrganization } = useSession();
  const navigate = useNavigate();
  const notify = useNotify();
  const accept = useMutation({
    mutationFn: async () => {
      const accepted = await unwrap(api.POST("/api/v1/invitations/accept", { body: { token } }));
      await switchOrganization(accepted.organization_id);
    },
    onSuccess: async () => {
      notify.success("Convite aceito. Bem-vindo à organização.");
      await navigate("/", { replace: true });
    },
  });

  return (
    <AuthLayout title="Aceitar convite">
      {token === "" ? (
        <p role="alert">Link de convite incompleto. Abra novamente o link recebido por e-mail.</p>
      ) : (
        <>
          <p>
            Você entrou como <strong>{me.email}</strong>. O convite só pode ser aceito pelo e-mail
            convidado.
          </p>
          <FormError message={accept.isError ? errorMessage(accept.error) : null} />
          <Button
            busy={accept.isPending}
            onClick={() => {
              accept.mutate();
            }}
          >
            Aceitar convite
          </Button>
        </>
      )}
    </AuthLayout>
  );
}
