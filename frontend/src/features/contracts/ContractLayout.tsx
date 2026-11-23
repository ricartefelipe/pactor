import { NavLink, Outlet, useOutletContext, useParams } from "react-router";

import type { Schemas } from "../../api/client";
import { useSession } from "../../auth/session";
import { Restricted } from "../../components/Restricted";
import { QueryView } from "../../components/states";
import { Badge, PageHeader } from "../../components/ui";
import { CONFIDENTIALITY, DRAFTING_STATUS, LIFECYCLE_STATUS } from "../../lib/labels";
import { useContract, useLifecycle } from "./queries";
import { draftingTone, lifecycleTone } from "./tones";

export type Contract = Schemas["Contract"];

const TABS = [
  { to: "", label: "Visão geral" },
  { to: "rascunho", label: "Rascunho" },
  { to: "revisoes", label: "Revisões e comentários" },
  { to: "aprovacao", label: "Aprovação" },
  { to: "assinatura", label: "Assinatura" },
  { to: "vigencia", label: "Vigência" },
  { to: "prazos", label: "Prazos e renovação" },
  { to: "obrigacoes", label: "Obrigações" },
  { to: "valores", label: "Valores", permission: "finance.read" },
  { to: "anexos", label: "Anexos" },
  { to: "acesso", label: "Acesso" },
] as const;

function LifecycleBadge({ id }: { id: string }) {
  const lifecycle = useLifecycle(id);
  if (!lifecycle.data) return null;
  const status = lifecycle.data.lifecycle_status;
  return <Badge tone={lifecycleTone(status)}>{LIFECYCLE_STATUS[status]}</Badge>;
}

function ContractShell({ id }: { id: string }) {
  const contract = useContract(id);
  const { can } = useSession();
  return (
    <QueryView query={contract}>
      {(data) => (
        <>
          <PageHeader
            title={`${data.reference} · ${data.title}`}
            description={
              <>
                {data.counterparty_name} ·{" "}
                <Badge tone={draftingTone(data.drafting_status)}>
                  {DRAFTING_STATUS[data.drafting_status]}
                </Badge>{" "}
                <LifecycleBadge id={id} />{" "}
                {data.confidentiality !== "internal" && (
                  <Badge tone="warning">{CONFIDENTIALITY[data.confidentiality]}</Badge>
                )}
              </>
            }
          />
          <nav aria-label="Seções do contrato" className="tabs">
            <ul>
              {TABS.filter((tab) => !("permission" in tab) || can(tab.permission)).map((tab) => (
                <li key={tab.to}>
                  <NavLink
                    to={tab.to === "" ? `/contratos/${id}` : `/contratos/${id}/${tab.to}`}
                    end
                  >
                    {tab.label}
                  </NavLink>
                </li>
              ))}
            </ul>
          </nav>
          <Outlet context={data} />
        </>
      )}
    </QueryView>
  );
}

export function ContractLayout() {
  const { id = "" } = useParams();
  return (
    <Restricted permission="contracts.read">
      <ContractShell id={id} />
    </Restricted>
  );
}

export function useContractContext(): Contract {
  return useOutletContext<Contract>();
}
