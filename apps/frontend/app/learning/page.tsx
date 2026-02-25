"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { DashboardShell } from "@/components/dashboard-shell";
import { DataTable } from "@/components/data-table";
import {
  LearningGitChange,
  LearningInsight,
  LearningJournalEntry,
  LearningProposal,
  ManagerCritique,
  Paginated,
  asPaginated,
  cpFetch,
} from "@/lib/api";

function shortHash(hash: string): string {
  if (!hash) return "-";
  return hash.slice(0, 10);
}

function riskClass(level: string): string {
  if (level === "safe") return "live";
  if (level === "moderate") return "warn";
  return "error";
}

function scoreColor(score: number | null): string {
  if (score === null) return "";
  if (score >= 7) return "live";
  if (score >= 4) return "warn";
  return "error";
}

function critiqueStatusClass(s: string): string {
  if (s === "deployed" || s === "approved") return "live";
  if (s === "pending") return "warn";
  if (s === "rejected" || s === "failed") return "error";
  return "";
}

export default function LearningPage() {
  const queryClient = useQueryClient();

  const critiquesQuery = useQuery({
    queryKey: ["learning", "critiques"],
    queryFn: () => cpFetch<Paginated<ManagerCritique> | ManagerCritique[]>("/learning/critiques/"),
  });

  const approveCritiqueMutation = useMutation({
    mutationFn: (id: number) =>
      cpFetch<{ ok: boolean }>(`/learning/critiques/${id}/approve/`, {
        method: "POST",
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["learning", "critiques"] });
      queryClient.invalidateQueries({ queryKey: ["commands"] });
    },
  });

  const rejectCritiqueMutation = useMutation({
    mutationFn: (id: number) =>
      cpFetch<{ ok: boolean }>(`/learning/critiques/${id}/reject/`, {
        method: "POST",
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["learning", "critiques"] });
      queryClient.invalidateQueries({ queryKey: ["commands"] });
    },
  });

  const proposalsQuery = useQuery({
    queryKey: ["learning", "proposals"],
    queryFn: () => cpFetch<Paginated<LearningProposal> | LearningProposal[]>("/learning/proposals/"),
  });

  const journalQuery = useQuery({
    queryKey: ["learning", "journal"],
    queryFn: () => cpFetch<Paginated<LearningJournalEntry> | LearningJournalEntry[]>("/learning/journal/"),
  });

  const insightsQuery = useQuery({
    queryKey: ["learning", "insights"],
    queryFn: () => cpFetch<Paginated<LearningInsight> | LearningInsight[]>("/learning/insights/?status=active"),
  });

  const gitChangesQuery = useQuery({
    queryKey: ["learning", "git-changes"],
    queryFn: () => cpFetch<Paginated<LearningGitChange> | LearningGitChange[]>("/learning/git-changes/"),
  });

  const approveMutation = useMutation({
    mutationFn: (id: number) =>
      cpFetch<{ ok: boolean }>(`/learning/proposals/${id}/approve/`, {
        method: "POST",
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["learning", "proposals"] });
      queryClient.invalidateQueries({ queryKey: ["commands"] });
    },
  });

  const rejectMutation = useMutation({
    mutationFn: (id: number) =>
      cpFetch<{ ok: boolean }>(`/learning/proposals/${id}/reject/`, {
        method: "POST",
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["learning", "proposals"] });
      queryClient.invalidateQueries({ queryKey: ["commands"] });
    },
  });

  const critiques = critiquesQuery.data ? asPaginated(critiquesQuery.data) : [];
  const proposals = proposalsQuery.data ? asPaginated(proposalsQuery.data) : [];
  const journal = journalQuery.data ? asPaginated(journalQuery.data) : [];
  const insights = insightsQuery.data ? asPaginated(insightsQuery.data) : [];
  const gitChanges = gitChangesQuery.data ? asPaginated(gitChangesQuery.data) : [];

  return (
    <DashboardShell>
      <section className="panel">
        <div className="panel-header">
          <h2>Apprentissage continu</h2>
        </div>
        <div className="panel-body stack">
          <div className="hint">
            Le bot apprend de ses erreurs, propose des corrections et peut pousser ses ameliorations sur Git.
          </div>
          <div className="grid">
            <article className="kpi">
              <div className="label">Propositions</div>
              <div className="value">{proposals.length}</div>
            </article>
            <article className="kpi">
              <div className="label">Insights actifs</div>
              <div className="value">{insights.length}</div>
            </article>
            <article className="kpi">
              <div className="label">Cycles journalises</div>
              <div className="value">{journal.length}</div>
            </article>
            <article className="kpi">
              <div className="label">Branches auto-learning</div>
              <div className="value">{gitChanges.length}</div>
            </article>
          </div>
        </div>
      </section>

      <DataTable
        title="Critiques du Manager"
        rows={critiques}
        columns={[
          { label: "Cycle", render: (row) => row.cycle_number },
          {
            label: "Trading",
            render: (row) => (
              <span className={`badge ${scoreColor(row.trading_quality_score)}`}>
                {row.trading_quality_score ?? "-"}/10
              </span>
            ),
          },
          {
            label: "Risk",
            render: (row) => (
              <span className={`badge ${scoreColor(row.risk_management_score)}`}>
                {row.risk_management_score ?? "-"}/10
              </span>
            ),
          },
          {
            label: "Strategie",
            render: (row) => (
              <span className={`badge ${scoreColor(row.strategy_effectiveness_score)}`}>
                {row.strategy_effectiveness_score ?? "-"}/10
              </span>
            ),
          },
          { label: "Resume", render: (row) => row.summary.slice(0, 120) || "-" },
          {
            label: "Statut",
            render: (row) => <span className={`badge ${critiqueStatusClass(row.status)}`}>{row.status}</span>,
          },
          { label: "Branche", render: (row) => row.branch_name || "-" },
          {
            label: "Decision",
            render: (row) => (
              <div className="mini-actions">
                <button
                  className="table-btn"
                  onClick={() => approveCritiqueMutation.mutate(row.id)}
                  disabled={
                    row.status !== "pending" ||
                    approveCritiqueMutation.isPending ||
                    rejectCritiqueMutation.isPending
                  }
                >
                  Approuver
                </button>
                <button
                  className="table-btn danger"
                  onClick={() => rejectCritiqueMutation.mutate(row.id)}
                  disabled={
                    row.status !== "pending" ||
                    approveCritiqueMutation.isPending ||
                    rejectCritiqueMutation.isPending
                  }
                >
                  Rejeter
                </button>
              </div>
            ),
          },
          { label: "Date", render: (row) => new Date(row.created_at).toLocaleString("fr-FR") },
        ]}
      />

      <DataTable
        title="Propositions d'amelioration"
        rows={proposals}
        columns={[
          { label: "Type", render: (row) => row.proposal_type },
          { label: "Cible", render: (row) => row.target },
          { label: "Avant", render: (row) => row.current_value || "-" },
          { label: "Apres", render: (row) => row.proposed_value },
          {
            label: "Risque",
            render: (row) => <span className={`badge ${riskClass(row.risk_level)}`}>{row.risk_level}</span>,
          },
          { label: "Statut", render: (row) => row.status },
          {
            label: "Decision",
            render: (row) => (
              <div className="mini-actions">
                <button
                  className="table-btn"
                  onClick={() => approveMutation.mutate(row.id)}
                  disabled={row.status !== "pending" || approveMutation.isPending || rejectMutation.isPending}
                >
                  Approuver
                </button>
                <button
                  className="table-btn danger"
                  onClick={() => rejectMutation.mutate(row.id)}
                  disabled={row.status !== "pending" || approveMutation.isPending || rejectMutation.isPending}
                >
                  Rejeter
                </button>
              </div>
            ),
          },
        ]}
      />

      <DataTable
        title="Historique Git des auto-corrections"
        rows={gitChanges}
        columns={[
          { label: "Branche", render: (row) => row.branch_name },
          { label: "Commit", render: (row) => shortHash(row.commit_hash) },
          { label: "Remote", render: (row) => row.remote_name },
          { label: "Push", render: (row) => row.push_status },
          { label: "Justification", render: (row) => row.justification.slice(0, 120) || "-" },
          { label: "Date", render: (row) => new Date(row.created_at).toLocaleString("fr-FR") },
        ]}
      />

      <DataTable
        title="Journal d'apprentissage"
        rows={journal}
        columns={[
          { label: "Cycle", render: (row) => row.cycle_number },
          { label: "Proposes", render: (row) => row.trades_proposed },
          { label: "Executes", render: (row) => row.trades_executed },
          { label: "Ignores", render: (row) => row.trades_skipped },
          {
            label: "Precision",
            render: (row) => (row.outcome_accuracy === null ? "-" : `${(row.outcome_accuracy * 100).toFixed(1)}%`),
          },
          { label: "Date", render: (row) => new Date(row.created_at).toLocaleString("fr-FR") },
        ]}
      />

      <DataTable
        title="Insights actifs"
        rows={insights}
        columns={[
          { label: "Type", render: (row) => row.insight_type },
          { label: "Severite", render: (row) => row.severity },
          { label: "Description", render: (row) => row.description.slice(0, 160) },
          { label: "Action proposee", render: (row) => row.proposed_action.slice(0, 160) || "-" },
          { label: "Date", render: (row) => new Date(row.created_at).toLocaleString("fr-FR") },
        ]}
      />

      {approveMutation.isError || rejectMutation.isError ? (
        <div>Impossible de traiter la decision de proposition.</div>
      ) : null}
      {approveCritiqueMutation.isError || rejectCritiqueMutation.isError ? (
        <div>Impossible de traiter la decision de critique.</div>
      ) : null}
    </DashboardShell>
  );
}
