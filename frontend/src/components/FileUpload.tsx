import { useQuery } from "@tanstack/react-query";
import { useEffect, useId, useState, type ChangeEvent } from "react";

import { api, unwrap, type Schemas } from "../api/client";
import { sameOrigin, withCsrf } from "../api/csrf";
import { errorMessage, networkError, toApiError } from "../api/errors";
import { useOrgKey } from "../app/queries";
import { formatBytes } from "../lib/format";
import { FILE_STATUS } from "../lib/labels";
import { Badge } from "./ui";

type StoredFile = Schemas["StoredFile"];

async function upload(file: File, purpose: Schemas["PurposeEnum"]): Promise<StoredFile> {
  const body = new FormData();
  body.set("file", file);
  body.set("purpose", purpose);
  let response: Response;
  try {
    response = await fetch(
      withCsrf(
        new Request(sameOrigin("/api/v1/files"), {
          method: "POST",
          credentials: "same-origin",
          body,
        }),
      ),
    );
  } catch {
    throw networkError();
  }
  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok) throw toApiError(response.status, payload);
  // Resposta do próprio servidor, no formato documentado (StoredFile).
  return payload as StoredFile;
}

/**
 * Envia um arquivo e acompanha a quarentena (antivírus e inspeção) até a
 * liberação ou recusa. Só arquivos liberados podem ser usados.
 */
export function FileUpload({
  label,
  purpose,
  onReleased,
}: {
  label: string;
  purpose: Schemas["PurposeEnum"];
  onReleased: (file: StoredFile) => void;
}) {
  const id = useId();
  const key = useOrgKey();
  const [fileId, setFileId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const status = useQuery({
    queryKey: key("files", fileId),
    queryFn: () =>
      unwrap(api.GET("/api/v1/files/{file_id}", { params: { path: { file_id: fileId ?? "" } } })),
    enabled: fileId !== null,
    refetchInterval: (query) => (query.state.data?.status === "quarantined" ? 1500 : false),
  });

  const current = status.data;
  useEffect(() => {
    if (current?.status === "released") onReleased(current);
  }, [current, onReleased]);

  async function handleChange(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;
    setError(null);
    setSending(true);
    try {
      setFileId((await upload(file, purpose)).id);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setSending(false);
    }
  }

  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <input
        id={id}
        type="file"
        accept=".pdf,.docx,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        onChange={(event) => void handleChange(event)}
        aria-describedby={`${id}-dica`}
        disabled={sending}
      />
      <p className="field__hint" id={`${id}-dica`}>
        PDF ou DOCX. O arquivo passa por antivírus e inspeção antes de ser liberado.
      </p>
      <div aria-live="polite">
        {sending && <p>Enviando…</p>}
        {current && (
          <p>
            {current.original_name} ({formatBytes(current.size)}):{" "}
            <Badge
              tone={
                current.status === "released"
                  ? "success"
                  : current.status === "quarantined"
                    ? "warning"
                    : "danger"
              }
            >
              {FILE_STATUS[current.status]}
            </Badge>
            {current.rejection_reason && (
              <> — {current.rejection_detail || current.rejection_reason}</>
            )}
          </p>
        )}
      </div>
      {error && (
        <p className="field__error" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
