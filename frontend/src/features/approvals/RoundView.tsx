import type { Schemas } from "../../api/client";
import { useMemberName } from "../../app/queries";
import { Badge, Table } from "../../components/ui";
import { formatDateTime } from "../../lib/format";
import { DECISION, ROUND_STATUS, STEP_STATE } from "../../lib/labels";

type Tone = "neutral" | "info" | "success" | "warning" | "danger";

const ROUND_TONE: Readonly<Record<Schemas["RoundStatusEnum"], Tone>> = {
  open: "warning",
  approved: "success",
  rejected: "danger",
  changes_requested: "warning",
  superseded: "neutral",
};

export function RoundStatusBadge({ status }: { status: Schemas["RoundStatusEnum"] }) {
  return <Badge tone={ROUND_TONE[status]}>{ROUND_STATUS[status]}</Badge>;
}

/** Etapas, aprovadores e votos de uma rodada (somente leitura). */
export function RoundView({ round }: { round: Schemas["Round"] }) {
  const memberName = useMemberName();
  return (
    <>
      <p>
        Revisão {round.revision_number} · <RoundStatusBadge status={round.status} /> · aberta em{" "}
        {formatDateTime(round.created_at)}
        {round.close_reason && <> · {round.close_reason}</>}
      </p>
      <ol className="steps">
        {round.steps.map((step) => (
          <li key={step.key} className={`step step--${step.state}`}>
            <p>
              <strong>{step.name}</strong> · {STEP_STATE[step.state]} · quórum {step.quorum}
            </p>
            <ul>
              {step.approvers.map((approver) => (
                <li key={approver.user_id}>
                  {memberName(approver.user_id)}
                  {!approver.active && <> (substituído: {approver.replaced_reason})</>}
                </li>
              ))}
            </ul>
          </li>
        ))}
      </ol>
      {round.votes.length > 0 && (
        <Table caption="Votos">
          <thead>
            <tr>
              <th scope="col">Etapa</th>
              <th scope="col">Aprovador</th>
              <th scope="col">Decisão</th>
              <th scope="col">Comentário</th>
              <th scope="col">Data</th>
            </tr>
          </thead>
          <tbody>
            {round.votes.map((vote) => (
              <tr key={vote.id}>
                <td>{round.steps.find((s) => s.key === vote.step_key)?.name ?? vote.step_key}</td>
                <td>
                  {memberName(vote.approver_id)}
                  {vote.cast_by_id !== vote.approver_id && (
                    <> (por {memberName(vote.cast_by_id)}, delegação)</>
                  )}
                </td>
                <td>{DECISION[vote.decision]}</td>
                <td>{vote.comment || "—"}</td>
                <td>{formatDateTime(vote.cast_at)}</td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
    </>
  );
}
