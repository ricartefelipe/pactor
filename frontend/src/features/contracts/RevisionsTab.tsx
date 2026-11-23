import { useMutation, useQuery } from "@tanstack/react-query";
import type { SubmitEvent } from "react";
import { useSearchParams } from "react-router";

import { api, unwrap, type Schemas } from "../../api/client";
import { errorMessage } from "../../api/errors";
import { useInvalidate, useOrgKey } from "../../app/queries";
import { useSession } from "../../auth/session";
import { FormError, SelectField, TextAreaField, TextField, formText } from "../../components/forms";
import { useNotify } from "../../components/notifications";
import { EmptyState, QueryView } from "../../components/states";
import { Badge, Button, DefinitionList, Section } from "../../components/ui";
import { formatDateTime } from "../../lib/format";
import { REVISION_SOURCE } from "../../lib/labels";
import { useContractContext } from "./ContractLayout";
import { useRevisions } from "./queries";

function Comments({ contractId, revisionId }: { contractId: string; revisionId: string }) {
  const key = useOrgKey();
  const { can } = useSession();
  const notify = useNotify();
  const invalidate = useInvalidate();
  const path = { params: { path: { id: contractId, revision_id: revisionId } } };
  const comments = useQuery({
    queryKey: key("contracts", "comments", revisionId),
    queryFn: () => unwrap(api.GET("/api/v1/contracts/{id}/revisions/{revision_id}/comments", path)),
  });
  const add = useMutation({
    mutationFn: (body: Schemas["CommentRequest"]) =>
      unwrap(
        api.POST("/api/v1/contracts/{id}/revisions/{revision_id}/comments", { ...path, body }),
      ),
    onSuccess: async () => {
      notify.success("Comentário registrado.");
      await invalidate("contracts", "comments", revisionId);
    },
  });
  const resolve = useMutation({
    mutationFn: (commentId: string) =>
      unwrap(
        api.POST("/api/v1/contracts/{id}/comments/{comment_id}/resolve", {
          params: { path: { id: contractId, comment_id: commentId } },
        }),
      ),
    onSuccess: async () => {
      notify.success("Comentário resolvido.");
      await invalidate("contracts", "comments", revisionId);
    },
  });

  function handleSubmit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const values = formText(form);
    const line = values.line ? Number(values.line) : undefined;
    add.mutate(
      { body: values.body ?? "", ...(line !== undefined ? { line } : {}) },
      {
        onSuccess: () => {
          form.reset();
        },
      },
    );
  }

  return (
    <Section title="Comentários desta revisão">
      <QueryView query={comments}>
        {(items) =>
          items.length === 0 ? (
            <EmptyState title="Nenhum comentário." />
          ) : (
            <ul className="comments">
              {items.map((comment) => (
                <li
                  key={comment.id}
                  className={comment.resolved_at ? "comment comment--resolved" : "comment"}
                >
                  <p className="comment__meta">
                    <strong>{comment.author_name}</strong>
                    {comment.line !== null && comment.line !== undefined && (
                      <> · linha {comment.line}</>
                    )}{" "}
                    · {formatDateTime(comment.created_at)}{" "}
                    {comment.resolved_at && <Badge tone="success">Resolvido</Badge>}
                  </p>
                  <p>{comment.body}</p>
                  {!comment.resolved_at && can("contracts.comment") && (
                    <Button
                      variant="link"
                      busy={resolve.isPending && resolve.variables === comment.id}
                      onClick={() => {
                        resolve.mutate(comment.id);
                      }}
                    >
                      Marcar como resolvido
                    </Button>
                  )}
                </li>
              ))}
            </ul>
          )
        }
      </QueryView>
      <FormError message={resolve.isError ? errorMessage(resolve.error) : null} />
      {can("contracts.comment") && (
        <form className="form" onSubmit={handleSubmit} noValidate>
          <FormError message={add.isError ? errorMessage(add.error) : null} />
          <TextAreaField label="Novo comentário" name="body" required rows={3} />
          <TextField label="Linha (opcional)" name="line" type="number" min={1} />
          <Button type="submit" busy={add.isPending}>
            Comentar
          </Button>
        </form>
      )}
    </Section>
  );
}

function RevisionDetail({ contractId, revisionId }: { contractId: string; revisionId: string }) {
  const key = useOrgKey();
  const revision = useQuery({
    queryKey: key("contracts", "revision", revisionId),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/contracts/{id}/revisions/{revision_id}", {
          params: { path: { id: contractId, revision_id: revisionId } },
        }),
      ),
  });
  return (
    <QueryView query={revision}>
      {(data) => (
        <>
          <Section title={`Revisão ${data.number}`}>
            <DefinitionList
              items={[
                ["Autor", data.author_name],
                ["Publicada em", formatDateTime(data.created_at)],
                ["Origem", REVISION_SOURCE[data.source]],
                ["Resumo", data.summary || "—"],
                [
                  "Impressão digital",
                  <code key="fp" className="secret">
                    {data.fingerprint}
                  </code>,
                ],
              ]}
            />
            <ol className="document document--numbered">
              {data.content.split("\n").map((line, index) => (
                <li key={index}>{line || " "}</li>
              ))}
            </ol>
          </Section>
          <Comments contractId={contractId} revisionId={revisionId} />
        </>
      )}
    </QueryView>
  );
}

function Comparison({
  contractId,
  oldId,
  newId,
}: {
  contractId: string;
  oldId: string;
  newId: string;
}) {
  const key = useOrgKey();
  const diff = useQuery({
    queryKey: key("contracts", "compare", contractId, oldId, newId),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/contracts/{id}/compare", {
          params: { path: { id: contractId }, query: { old: oldId, new: newId } },
        }),
      ),
  });
  return (
    <QueryView query={diff}>
      {(data) => (
        <div className="diff" aria-label="Diferenças entre revisões">
          <p>
            <Badge tone="success">+{data.inserted_lines} linhas</Badge>{" "}
            <Badge tone="danger">−{data.deleted_lines} linhas</Badge>
          </p>
          {data.hunks.map((hunk, index) => (
            <div key={index} className={`diff__hunk diff__hunk--${hunk.kind}`}>
              {hunk.old_lines.map((line, i) => (
                <p
                  key={`o${i}`}
                  className={
                    hunk.kind === "equal" ? "diff__line" : "diff__line diff__line--deleted"
                  }
                >
                  <span className="visually-hidden">
                    {hunk.kind === "equal" ? "Sem alteração: " : "Removido: "}
                  </span>
                  <span aria-hidden="true">{hunk.kind === "equal" ? "  " : "− "}</span>
                  {line}
                </p>
              ))}
              {hunk.kind !== "equal" &&
                hunk.new_lines.map((line, i) => (
                  <p key={`n${i}`} className="diff__line diff__line--inserted">
                    <span className="visually-hidden">Inserido: </span>
                    <span aria-hidden="true">+ </span>
                    {line}
                  </p>
                ))}
            </div>
          ))}
        </div>
      )}
    </QueryView>
  );
}

export function RevisionsTab() {
  const contract = useContractContext();
  const revisions = useRevisions(contract.id);
  const [params, setParams] = useSearchParams();

  return (
    <QueryView query={revisions}>
      {(items) => {
        if (items.length === 0) {
          return (
            <EmptyState title="Nenhuma revisão publicada. Publique o rascunho para iniciar o histórico." />
          );
        }
        const selected = params.get("revisao") ?? items[0]?.id ?? "";
        const oldId = params.get("antiga");
        const newId = params.get("nova");
        const revisionOptions = items.map((r) => ({ value: r.id, label: `Revisão ${r.number}` }));
        return (
          <>
            <Section title="Histórico">
              <ul className="revision-list">
                {items.map((revision) => (
                  <li key={revision.id}>
                    <Button
                      variant={revision.id === selected ? "primary" : "secondary"}
                      aria-pressed={revision.id === selected}
                      onClick={() => {
                        setParams({ revisao: revision.id });
                      }}
                    >
                      Revisão {revision.number}
                    </Button>{" "}
                    {revision.author_name} · {formatDateTime(revision.created_at)}
                    {contract.approved_revision_id === revision.id && (
                      <>
                        {" "}
                        <Badge tone="success">Aprovada</Badge>
                      </>
                    )}
                  </li>
                ))}
              </ul>
            </Section>
            {items.length > 1 && (
              <Section title="Comparar revisões">
                <form
                  className="filters"
                  onSubmit={(event) => {
                    event.preventDefault();
                    const values = formText(event.currentTarget);
                    setParams({
                      revisao: selected,
                      antiga: values.antiga ?? "",
                      nova: values.nova ?? "",
                    });
                  }}
                >
                  <SelectField
                    label="Revisão anterior"
                    name="antiga"
                    defaultValue={oldId ?? items[1]?.id ?? ""}
                    options={revisionOptions}
                  />
                  <SelectField
                    label="Revisão nova"
                    name="nova"
                    defaultValue={newId ?? items[0]?.id ?? ""}
                    options={revisionOptions}
                  />
                  <Button type="submit" variant="secondary">
                    Comparar
                  </Button>
                </form>
                {oldId && newId && (
                  <Comparison contractId={contract.id} oldId={oldId} newId={newId} />
                )}
              </Section>
            )}
            <RevisionDetail key={selected} contractId={contract.id} revisionId={selected} />
          </>
        );
      }}
    </QueryView>
  );
}
