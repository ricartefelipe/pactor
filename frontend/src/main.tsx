import { MutationCache, QueryCache, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { RouterProvider } from "react-router";

import { ApiError } from "./api/errors";
import { router } from "./app/router";
import { AUTH_KEY } from "./auth/session";
import { NotificationsProvider } from "./components/notifications";
import "@fontsource-variable/inter";
import "./styles.css";

function handleUnauthenticated(error: unknown) {
  if (error instanceof ApiError && error.status === 401) {
    queryClient.setQueryData(AUTH_KEY, "anonymous");
  }
}

const queryClient = new QueryClient({
  queryCache: new QueryCache({ onError: handleUnauthenticated }),
  mutationCache: new MutationCache({ onError: handleUnauthenticated }),
  defaultOptions: {
    queries: {
      staleTime: 15_000,
      refetchOnWindowFocus: false,
      // Erros 4xx são respostas definitivas; só falhas de rede ou do servidor são repetidas.
      retry: (failures, error) =>
        failures < 2 && !(error instanceof ApiError && error.status >= 400 && error.status < 500),
    },
  },
});

const root = document.getElementById("root");
if (root === null) throw new Error("Elemento #root ausente");

createRoot(root).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <NotificationsProvider>
        <RouterProvider router={router} />
      </NotificationsProvider>
    </QueryClientProvider>
  </StrictMode>,
);
