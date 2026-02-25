"use client";

import { useQuery } from "@tanstack/react-query";

import { DashboardShell } from "@/components/dashboard-shell";
import { DataTable } from "@/components/data-table";
import { Paginated, PerformanceSnapshot, asPaginated, cpFetch } from "@/lib/api";

export default function PerformancePage() {
  const query = useQuery({
    queryKey: ["performance"],
    queryFn: () => cpFetch<Paginated<PerformanceSnapshot> | PerformanceSnapshot[]>("/performance/"),
  });

  return (
    <DashboardShell>
      {query.isLoading ? <div>Chargement des snapshots de performance...</div> : null}
      {query.isError ? <div>Impossible de charger les snapshots de performance.</div> : null}
      {query.data ? (
        <DataTable
          title="Snapshots de performance"
          rows={asPaginated(query.data)}
          columns={[
            { label: "Type", render: (row) => row.snapshot_type },
            {
              label: "Resume",
              render: (row) => JSON.stringify(row.payload).slice(0, 140),
            },
            { label: "Capture", render: (row) => new Date(row.created_at).toLocaleString("fr-FR") },
          ]}
        />
      ) : null}
    </DashboardShell>
  );
}
