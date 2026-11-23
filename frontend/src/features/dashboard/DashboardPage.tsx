import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router";

import { api, unwrap } from "../../api/client";
import { useOrgKey } from "../../app/queries";
import { useActiveOrganization, useSession } from "../../auth/session";
import { EmptyState, QueryView } from "../../components/states";
import { Badge, PageHeader, Section, Table } from "../../components/ui";
import { formatDate, formatDateTime } from "../../lib/format";
import {
  ALERT_KIND,
  ALERT_STATUS,
  DRAFTING_STATUS,
  LIFECYCLE_STATUS,
  labelOf,
} from "../../lib/labels";

function Metric({ label, value, to }: { label: string; value: number; to?: string }) {
  return (
    <div className="metric">
      <p className="metric__value">{to ? <Link to={to}>{value}</Link> : value}</p>
      <p className="metric__label">{label}</p>
    </div>
  );
}

function Distribution<K extends string>({
  title,
  counts,
  labels,
}: {
  title: string;
  counts: Readonly<Record<string, number>>;
  labels: Readonly<Record<K, string>>;
}) {
  const entries = Object.entries(counts).filter(([, value]) => value > 0);
  return (
    <div>
      <h3>{title}</h3>
      {entries.length === 0 ? (
        <p>Nenhum contrato.</p>
      ) : (
        <ul className="distribution">
          {entries.map(([status, value]) => (
            <li key={status}>
              <span>{labelOf(labels, status)}</span> <strong>{value}</strong>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function DashboardPage() {
  const key = useOrgKey();
  const { can } = useSession();
  const active = useActiveOrganization();
  const dashboard = useQuery({
    queryKey: key("dashboard"),
    queryFn: () => unwrap(api.GET("/api/v1/reports/dashboard")),
  });
  const alerts = useQuery({
    queryKey: key("alerts"),
    queryFn: () => unwrap(api.GET("/api/v1/alerts")),
    enabled: can("contracts.read"),
  });

  return (
    <>
      <PageHeader title="Painel" description={active.name} />
      <QueryView query={dashboard}>
        {(data) => (
          <>
            <div className="metrics">
              <Metric label="Contratos visíveis" value={data.contracts_total} to="/contratos" />
              <Metric
                label="Aprovações aguardando você"
                value={data.pending_approvals}
                to="/aprovacoes"
              />
              <Metric
                label="Prazos nos próximos 30 dias"
                value={data.deadlines_next_30_days}
                to="/calendario"
              />
              <Metric
                label="Obrigações em atraso"
                value={data.overdue_obligations}
                to="/calendario"
              />
            </div>
            <Section title="Situação dos contratos">
              <div className="columns">
                <Distribution
                  title="Elaboração"
                  counts={data.by_drafting_status}
                  labels={DRAFTING_STATUS}
                />
                <Distribution
                  title="Vigência"
                  counts={data.by_lifecycle_status}
                  labels={LIFECYCLE_STATUS}
                />
              </div>
              <p className="muted">Dados de {formatDate(data.today)}.</p>
            </Section>
          </>
        )}
      </QueryView>
      {can("contracts.read") && (
        <Section title="Alertas recentes">
          <QueryView query={alerts}>
            {(items) =>
              items.length === 0 ? (
                <EmptyState title="Nenhum alerta programado." />
              ) : (
                <Table caption="Alertas recentes">
                  <thead>
                    <tr>
                      <th scope="col">Assunto</th>
                      <th scope="col">Tipo</th>
                      <th scope="col">Disparo</th>
                      <th scope="col">Situação</th>
                    </tr>
                  </thead>
                  <tbody>
                    {items.slice(0, 10).map((alert) => (
                      <tr key={alert.id}>
                        <td>
                          <Link to={`/contratos/${alert.contract_id}`}>{alert.subject}</Link>
                        </td>
                        <td>{ALERT_KIND[alert.kind]}</td>
                        <td>{formatDateTime(alert.fire_at)}</td>
                        <td>
                          <Badge tone={alert.status === "sent" ? "success" : "neutral"}>
                            {ALERT_STATUS[alert.status]}
                          </Badge>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </Table>
              )
            }
          </QueryView>
        </Section>
      )}
    </>
  );
}
