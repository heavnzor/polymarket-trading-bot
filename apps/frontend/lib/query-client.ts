import { QueryClient } from "@tanstack/react-query";

export function makeQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 8_000,
        refetchInterval: 15_000,
        refetchOnWindowFocus: true,
      },
    },
  });
}
