import { useMutation, useQuery } from "@tanstack/react-query";
import { useCallback, useState } from "react";

import { api, unwrap, unwrapEmpty, type Schemas } from "../../api/client";
import { errorMessage } from "../../api/errors";
import { useInvalidate, useOrgKey } from "../../app/queries";
import { FileUpload } from "../../components/FileUpload";
import { CheckboxField, FormError } from "../../components/forms";
import { useNotify } from "../../components/notifications";
import { EmptyState, QueryView } from "../../components/states";
import { Badge, Button, Section, Table } from "../../components/ui";
import { formatBytes, formatDateTime } from "../../lib/format";
import { FILE_STATUS } from "../../lib/labels";
import { useContractContext } from "./ContractLayout";

export function AttachmentsTab() {
  const contract = useContractContext();
  const key = useOrgKey();
  const notify = useNotify();
  const invalidate = useInvalidate();
  const [released, setReleased] = useState<Schemas["StoredFile"] | null>(null);
  const [include, setInclude] = useState(true);
  const path = { params: { path: { contract_id: contract.id } } };
  const attachments = useQuery({
    queryKey: key("contracts", "attachments", contract.id),
    queryFn: () => unwrap(api.GET("/api/v1/contracts/{contract_id}/attachments", path)),
  });
  const attach = useMutation({
    mutationFn: (fileId: string) =>
      unwrap(
        api.POST("/api/v1/contracts/{contract_id}/attachments", {
          ...path,
          body: { file_id: fileId, include_in_submission: include },
        }),
      ),
    onSuccess: async () => {
      setReleased(null);
      notify.success("Arquivo anexado ao contrato.");
      await invalidate("contracts");
    },
  });
  const remove = useMutation({
    mutationFn: (attachmentId: string) =>
      unwrapEmpty(
        api.DELETE("/api/v1/contracts/{contract_id}/attachments/{attachment_id}", {
          params: { path: { contract_id: contract.id, attachment_id: attachmentId } },
        }),
      ),
    onSuccess: async () => {
      notify.success("Anexo removido.");
      await invalidate("contracts");
    },
  });
  const onReleased = useCallback((file: Schemas["StoredFile"]) => {
    setReleased(file);
  }, []);

  return (
    <>
      <Section title="Anexos">
        <QueryView query={attachments}>
          {(items) =>
            items.length === 0 ? (
              <EmptyState title="Nenhum anexo." />
            ) : (
              <Table caption="Anexos do contrato">
                <thead>
                  <tr>
                    <th scope="col">Arquivo</th>
                    <th scope="col">Situação</th>
                    <th scope="col">Entra na aprovação</th>
                    <th scope="col">Adicionado em</th>
                    <th scope="col">
                      <span className="visually-hidden">Ações</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((attachment) => (
                    <tr key={attachment.id}>
                      <td>
                        {attachment.file.status === "released" ? (
                          <a href={`/api/v1/files/${attachment.file.id}/content`}>
                            {attachment.file.original_name}
                          </a>
                        ) : (
                          attachment.file.original_name
                        )}{" "}
                        ({formatBytes(attachment.file.size)})
                      </td>
                      <td>
                        <Badge tone={attachment.file.status === "released" ? "success" : "warning"}>
                          {FILE_STATUS[attachment.file.status]}
                        </Badge>
                      </td>
                      <td>{attachment.include_in_submission ? "Sim" : "Não"}</td>
                      <td>{formatDateTime(attachment.created_at)}</td>
                      <td>
                        {contract.can_edit && (
                          <Button
                            variant="link"
                            busy={remove.isPending && remove.variables === attachment.id}
                            onClick={() => {
                              remove.mutate(attachment.id);
                            }}
                          >
                            Remover
                          </Button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </Table>
            )
          }
        </QueryView>
        <FormError message={remove.isError ? errorMessage(remove.error) : null} />
      </Section>
      {contract.can_edit && (
        <Section title="Adicionar anexo">
          <p className="muted">
            Anexos marcados para a aprovação entram na impressão digital da revisão: incluir ou
            remover um deles exige nova aprovação.
          </p>
          <FileUpload label="Arquivo" purpose="contract_attachment" onReleased={onReleased} />
          <CheckboxField
            label="Incluir na aprovação e na assinatura"
            name="include"
            checked={include}
            onChange={(event) => {
              setInclude(event.target.checked);
            }}
          />
          <FormError message={attach.isError ? errorMessage(attach.error) : null} />
          <Button
            disabled={released === null}
            busy={attach.isPending}
            onClick={() => {
              if (released) attach.mutate(released.id);
            }}
          >
            Anexar arquivo liberado
          </Button>
        </Section>
      )}
    </>
  );
}
