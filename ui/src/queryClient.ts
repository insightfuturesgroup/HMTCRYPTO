import { QueryClient } from "@tanstack/react-query";

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      gcTime: 5 * 60_000,
      refetchOnWindowFocus: true,
      retry: 1,
      staleTime: 15_000,
    },
    mutations: { retry: false },
  },
});

export function queryStaleTime(path: string): number {
  if (path.startsWith("/api/v1/market/products")) return 2_000;
  if (path.startsWith("/api/v1/accounts/") && path.endsWith("/state")) return 1_000;
  if (path.startsWith("/api/v1/accounts")) return 15_000;
  if (path.startsWith("/api/v1/admin/") || path === "/api/v1/settings") return 30_000;
  return 5_000;
}

export function invalidateApplicationQueries(): Promise<void> {
  return queryClient.invalidateQueries({
    predicate: (query) => String(query.queryKey[0] ?? "").startsWith("/api/v1/"),
  });
}
