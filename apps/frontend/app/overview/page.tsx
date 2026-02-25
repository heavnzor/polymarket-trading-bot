"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { DashboardShell } from "@/components/dashboard-shell";
import { RealtimeFeed } from "@/components/realtime-feed";
import { BotCommand, Overview, cpFetch } from "@/lib/api";

function metric(label: string, value: string) {
  return (
    <article className="kpi" key={label}>
      <div className="label">{label}</div>
      <div className="value">{value}</div>
    </article>
  );
}

function statusLabel(status: string): string {
  if (status === "running") return "Actif";
  if (status === "paused") return "En pause";
  return "Arrete";
}

export default function OverviewPage() {
  const queryClient = useQueryClient();
  const overviewQuery = useQuery({
    queryKey: ["overview"],
    queryFn: () => cpFetch<Overview>("/overview/"),
  });

  const commandMutation = useMutation({
    mutationFn: (command: string) =>
      cpFetch<BotCommand>("/commands/", {
        method: "POST",
        body: JSON.stringify({ command, payload: {} }),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["overview"] });
      queryClient.invalidateQueries({ queryKey: ["commands"] });
    },
  });

  const data = overviewQuery.data;

  return (
    <DashboardShell>
      <section className="panel">
        <div className="panel-header">
          <h2>Pilotage rapide</h2>
          <span className={`badge ${overviewQuery.isError ? "error" : "live"}`}>
            {overviewQuery.isError ? "Erreur sync" : "Synchronise"}
          </span>
        </div>
        <div className="panel-body stack">
          <div className="hint">
            Utilise ces boutons pour piloter le bot sans entrer de commande manuelle.
          </div>
          <div className="button-row">
            <button
              onClick={() => commandMutation.mutate("stop_process")}
              disabled={commandMutation.isPending}
            >
              Stopper le processus
            </button>
            <button
              onClick={() => commandMutation.mutate("start_process")}
              disabled={commandMutation.isPending}
            >
              Demarrer le processus
            </button>
            <button
              onClick={() => commandMutation.mutate("force_cycle")}
              disabled={commandMutation.isPending}
            >
              Lancer un cycle
            </button>
            <button
              onClick={() => commandMutation.mutate("pause")}
              disabled={commandMutation.isPending}
            >
              Mettre en pause
            </button>
            <button
              onClick={() => commandMutation.mutate("resume")}
              disabled={commandMutation.isPending}
            >
              Reprendre
            </button>
            <button
              onClick={() => commandMutation.mutate("toggle_paper")}
              disabled={commandMutation.isPending}
            >
              Basculer reel/simulation
            </button>
          </div>
          {commandMutation.isError ? <div>Impossible d'envoyer la commande.</div> : null}
        </div>
      </section>

      <section className="panel">
        <div className="panel-header">
          <h2>Etat global</h2>
        </div>
        <div className="panel-body">
          {overviewQuery.isLoading ? <div>Chargement des indicateurs...</div> : null}
          {overviewQuery.isError ? <div>Impossible de charger l'etat du bot.</div> : null}
          {data ? (
            <div className="grid">
              {metric("Statut bot", statusLabel(data.bot_status))}
              {metric("Strategie", "active (achat + vente)")}
              {metric("Cycle", `${data.cycle_number} (${data.cycle_interval_minutes} min)`)}
              {metric("USDC disponible", data.available_usdc.toFixed(2))}
              {metric("Solde on-chain", data.onchain_balance?.toFixed(2) ?? "n/a")}
              {metric("Valeur portefeuille", data.portfolio_value.toFixed(2))}
              {metric("PnL du jour", data.daily_pnl.toFixed(2))}
              {metric("PnL total", data.total_pnl.toFixed(2))}
              {metric("Taux de reussite", `${(data.hit_rate * 100).toFixed(1)}%`)}
              {metric("ROI", `${data.roi_percent.toFixed(2)}%`)}
              {metric("Nombre de trades", String(data.total_trades))}
              {metric("Mode", data.is_paper ? "Simulation" : "Reel")}
            </div>
          ) : null}
        </div>
      </section>

      <RealtimeFeed />
    </DashboardShell>
  );
}
