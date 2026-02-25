"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { DashboardShell } from "@/components/dashboard-shell";
import { DataTable } from "@/components/data-table";
import { Paginated, Position, asPaginated, cpFetch } from "@/lib/api";

type CloseResponse = {
  ok: boolean;
  command_id: number;
  position_id: number;
  position_legacy_id: number | null;
};

export default function PositionsPage() {
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: ["positions"],
    queryFn: () => cpFetch<Paginated<Position> | Position[]>("/positions/"),
  });

  const closeMutation = useMutation({
    mutationFn: (positionId: number) =>
      cpFetch<CloseResponse>(`/positions/${positionId}/close/`, {
        method: "POST",
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["positions"] });
      queryClient.invalidateQueries({ queryKey: ["commands"] });
    },
  });

  return (
    <DashboardShell>
      <section className="panel">
        <div className="panel-header">
          <h2>Positions ouvertes</h2>
        </div>
        <div className="panel-body">
          <div className="hint">
            Tu peux fermer une position manuellement. Le bot tentera une vente puis mettra a jour l'etat.
          </div>
        </div>
      </section>

      {query.isLoading ? <div>Chargement des positions...</div> : null}
      {query.isError ? <div>Impossible de charger les positions.</div> : null}
      {closeMutation.isError ? <div>Echec de la demande de fermeture.</div> : null}

      {query.data ? (
        <DataTable
          title="Portefeuille"
          rows={asPaginated(query.data)}
          columns={[
            { label: "Marche", render: (row) => row.market_id },
            { label: "Issue", render: (row) => row.outcome },
            { label: "Taille", render: (row) => row.size },
            { label: "Prix moyen", render: (row) => row.avg_price },
            { label: "Statut", render: (row) => row.status },
            { label: "Strategie", render: () => "active" },
            { label: "Maj", render: (row) => new Date(row.updated_at).toLocaleString("fr-FR") },
            {
              label: "Action",
              render: (row) => (
                <button
                  className="table-btn"
                  onClick={() => closeMutation.mutate(row.id)}
                  disabled={closeMutation.isPending || row.status !== "open"}
                >
                  Fermer
                </button>
              ),
            },
          ]}
        />
      ) : null}
    </DashboardShell>
  );
}
