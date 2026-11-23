import { useQuery } from "@tanstack/react-query";
import { CircleCheck, Clock, FileText, TriangleAlert, type LucideIcon } from "lucide-react";
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

type MetricTone = "indigo" | "amber" | "sky" | "rose";

function Metric({
  label,
  value,
  to,
  icon: Icon,
  tone,
}: {
  label: string;
  value: number;
  to: string;
  icon: LucideIcon;
  tone: MetricTone;
}) {
  return (
    <Link to={to} className={`metric metric--${tone}`}>
      <span className="metric__icon" aria-hidden="true">
        <Icon size={22} />
      </span>
      <span className="metric__body">
        <span className="metric__value">{value}</span>
        <span className="metric__label">{label}</span>
      </span>
    </Link>
  );
}

function Distribution({
  title,
  counts,
  labels,
}: {
  title: string;
  counts: Readonly<Record<string, number>>;
  labels: Readonly<Record<string, string>>;
}) {
  const entries = Object.entries(counts).filter(([, value]) => value > 0);
  const total = entries.reduce((sum, [, value]) => sum + value, 0);
  return (
    <div className="distribution">
      <h3>{title}</h3>
      {entries.length === 0 ? (
        <p className="muted">Nenhum contrato.</p>
      ) : (
        <ul>
          {entries.map(([status, value]) => (
            <li key={status}>
              <span className="distribution__label">{labelOf(labels, status)}</span>
              <progress
                max={total}
                value={value}
                aria-label={`${labelOf(labels, status)}: ${String(value)} de ${String(total)}`}
              />
              <strong>{value}</strong>
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
              <Metric
                label="Contratos visíveis"
                value={data.contracts_total}
                to="/contratos"
                icon={FileText}
                tone="indigo"
              />
              <Metric
                label="Aprovações aguardando você"
                value={data.pending_approvals}
                to="/aprovacoes"
                icon={CircleCheck}
                tone="sky"
              />
              <Metric
                label="Prazos nos próximos 30 dias"
                value={data.deadlines_next_30_days}
                to="/calendario"
                icon={Clock}
                tone="amber"
              />
              <Metric
                label="Obrigações em atraso"
                value={data.overdue_obligations}
                to="/calendario"
                icon={TriangleAlert}
                tone="rose"
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
