import { useQuery } from "@tanstack/react-query";

import { api, unwrap } from "../../api/client";
import { useOrgKey } from "../../app/queries";
import { Restricted } from "../../components/Restricted";
import { QueryView } from "../../components/states";
import { PageHeader, Section, Table } from "../../components/ui";
import { formatDate } from "../../lib/format";
import { DRAFTING_STATUS, LIFECYCLE_STATUS, labelOf } from "../../lib/labels";

function CountTable<K extends string>({
  caption,
  counts,
  labels,
}: {
  caption: string;
  counts: Readonly<Record<string, number>>;
  labels: Readonly<Record<K, string>>;
}) {
  const rows = Object.entries(counts);
  return (
    <Table caption={caption}>
      <thead>
        <tr>
          <th scope="col">Situação</th>
          <th scope="col">Contratos</th>
        </tr>
      </thead>
      <tbody>
        {rows.length === 0 ? (
          <tr>
            <td colSpan={2}>Nenhum contrato.</td>
          </tr>
        ) : (
          rows.map(([status, count]) => (
            <tr key={status}>
              <td>{labelOf(labels, status)}</td>
              <td>{count}</td>
            </tr>
          ))
        )}
      </tbody>
    </Table>
  );
}

function Reports() {
  const key = useOrgKey();
  const dashboard = useQuery({
    queryKey: key("dashboard"),
    queryFn: () => unwrap(api.GET("/api/v1/reports/dashboard")),
  });
  return (
    <>
      <PageHeader
        title="Relatórios"
        actions={
          <a className="button button--secondary" href="/api/v1/reports/contracts.csv" download>
            Exportar contratos (CSV)
          </a>
        }
        description="Contagens e exportação consideram somente os contratos que você pode ver."
      />
      <QueryView query={dashboard}>
        {(data) => (
          <>
            <p className="muted">
              Posição em {formatDate(data.today)}: {data.contracts_total} contratos visíveis.
            </p>
            <div className="columns">
              <Section title="Por elaboração">
                <CountTable
                  caption="Contratos por situação de elaboração"
                  counts={data.by_drafting_status}
                  labels={DRAFTING_STATUS}
                />
              </Section>
              <Section title="Por vigência">
                <CountTable
                  caption="Contratos por situação de vigência"
                  counts={data.by_lifecycle_status}
                  labels={LIFECYCLE_STATUS}
                />
              </Section>
            </div>
          </>
        )}
      </QueryView>
    </>
  );
}

export function ReportsPage() {
  return (
    <Restricted permission="reports.read">
      <Reports />
    </Restricted>
  );
}
