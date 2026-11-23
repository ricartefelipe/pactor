import { useQuery } from "@tanstack/react-query";

import { api, unwrap } from "../../api/client";
import { useOrgKey } from "../../app/queries";

export function useContract(id: string) {
  const key = useOrgKey();
  return useQuery({
    queryKey: key("contracts", "detail", id),
    queryFn: () => unwrap(api.GET("/api/v1/contracts/{id}", { params: { path: { id } } })),
  });
}

export function useRevisions(id: string) {
  const key = useOrgKey();
  return useQuery({
    queryKey: key("contracts", "revisions", id),
    queryFn: () =>
      unwrap(api.GET("/api/v1/contracts/{id}/revisions", { params: { path: { id } } })),
  });
}

export function useLifecycle(id: string) {
  const key = useOrgKey();
  return useQuery({
    queryKey: key("contracts", "lifecycle", id),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/contracts/{contract_id}/lifecycle", {
          params: { path: { contract_id: id } },
        }),
      ),
  });
}
