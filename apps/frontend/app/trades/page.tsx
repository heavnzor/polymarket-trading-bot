"use client";

import { useQuery } from "@tanstack/react-query";

import { DashboardShell } from "@/components/dashboard-shell";
import { DataTable } from "@/components/data-table";
import { Paginated, Trade, asPaginated, cpFetch } from "@/lib/api";

export default function TradesPage() {
  const query = useQuery({
    queryKey: ["trades"],
    queryFn: () => cpFetch<Paginated<Trade> | Trade[]>("/trades/"),
  });

  return (
    <DashboardShell>
      {query.isLoading ? <div>Chargement des transactions...</div> : null}
      {query.isError ? <div>Impossible de charger les transactions.</div> : null}
      {query.data ? (
        <DataTable
          title="Historique des transactions"
          rows={asPaginated(query.data)}
          columns={[
            { label: "Marche", render: (row) => row.market_id },
            { label: "Cote", render: (row) => row.side },
            { label: "Issue", render: (row) => row.outcome },
            { label: "Montant USDC", render: (row) => row.size_usdc },
            { label: "Prix", render: (row) => row.price },
            { label: "Statut", render: (row) => row.status },
            { label: "Strategie", render: () => "active" },
            { label: "Date", render: (row) => new Date(row.created_at).toLocaleString("fr-FR") },
          ]}
        />
      ) : null}
    </DashboardShell>
  );
}
