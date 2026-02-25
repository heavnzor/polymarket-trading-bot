"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChangeEvent, useMemo, useState } from "react";

import { DashboardShell } from "@/components/dashboard-shell";
import { BotSetting, Paginated, asPaginated, cpFetch } from "@/lib/api";

type Meta = {
  label_fr?: string;
  description_fr?: string;
  category?: string;
  value_type?: string;
  choices?: string;
  min_value?: number;
  max_value?: number;
};

function parseChoices(raw: unknown): string[] {
  if (Array.isArray(raw)) return raw.map(String);
  if (typeof raw !== "string" || !raw.trim()) return [];
  try {
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.map(String) : [];
  } catch {
    return [];
  }
}

function toNumberOrEmpty(value: string): string {
  if (value.trim() === "") return "";
  const num = Number(value);
  return Number.isFinite(num) ? String(num) : "";
}

export default function SettingsPage() {
  const queryClient = useQueryClient();
  const [drafts, setDrafts] = useState<Record<string, string>>({});

  const query = useQuery({
    queryKey: ["settings"],
    queryFn: () => cpFetch<Paginated<BotSetting> | BotSetting[]>("/settings/"),
  });

  const mutation = useMutation({
    mutationFn: ({ key, value }: { key: string; value: string }) =>
      cpFetch<BotSetting>(`/settings/${encodeURIComponent(key)}/`, {
        method: "PATCH",
        body: JSON.stringify({ value }),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["settings"] });
    },
  });

  const rows = query.data ? asPaginated(query.data) : [];

  const groups = useMemo(() => {
    const map = new Map<string, BotSetting[]>();
    for (const row of rows) {
      const meta = (row.metadata ?? {}) as Meta;
      const category = meta.category || "autres";
      const list = map.get(category) ?? [];
      list.push(row);
      map.set(category, list);
    }
    return Array.from(map.entries());
  }, [rows]);

  function currentValue(row: BotSetting): string {
    return drafts[row.key] ?? row.value;
  }

  function updateDraft(key: string, value: string) {
    setDrafts((prev) => ({ ...prev, [key]: value }));
  }

  function onInputChange(row: BotSetting, event: ChangeEvent<HTMLInputElement | HTMLSelectElement>) {
    const meta = (row.metadata ?? {}) as Meta;
    const valueType = meta.value_type || "text";

    if (valueType === "bool" && event.target instanceof HTMLInputElement) {
      updateDraft(row.key, event.target.checked ? "true" : "false");
      return;
    }

    let value = event.target.value;
    if (valueType === "int") {
      value = toNumberOrEmpty(value);
    }
    updateDraft(row.key, value);
  }

  return (
    <DashboardShell>
      <section className="panel">
        <div className="panel-header">
          <h2>Parametres du bot</h2>
        </div>
        <div className="panel-body">
          <div className="hint">
            Tous les reglages utiles sont modifiables ici. Chaque changement est pris en compte par le bot.
          </div>
        </div>
      </section>

      {query.isLoading ? <div>Chargement des parametres...</div> : null}
      {query.isError ? <div>Impossible de charger les parametres.</div> : null}

      {groups.map(([category, categoryRows]) => (
        <section className="panel" key={category}>
          <div className="panel-header">
            <h2>{category.toUpperCase()}</h2>
          </div>
          <div className="panel-body stack">
            {categoryRows.map((row) => {
              const meta = (row.metadata ?? {}) as Meta;
              const valueType = meta.value_type || "text";
              const choices = parseChoices(meta.choices);
              const value = currentValue(row);

              return (
                <article className="setting-card" key={row.key}>
                  <div className="setting-head">
                    <div>
                      <div className="setting-title">{meta.label_fr || row.key}</div>
                      <div className="setting-key">{row.key}</div>
                    </div>
                    <button
                      className="table-btn"
                      onClick={() => mutation.mutate({ key: row.key, value })}
                      disabled={mutation.isPending}
                    >
                      Enregistrer
                    </button>
                  </div>

                  {meta.description_fr ? <div className="hint">{meta.description_fr}</div> : null}

                  <div className="input-row">
                    {valueType === "choice" ? (
                      <select value={value} onChange={(event) => onInputChange(row, event)}>
                        {choices.map((choice) => (
                          <option key={choice} value={choice}>
                            {choice}
                          </option>
                        ))}
                      </select>
                    ) : null}

                    {valueType === "bool" ? (
                      <label className="checkbox-row">
                        <input
                          type="checkbox"
                          checked={value === "true"}
                          onChange={(event) => onInputChange(row, event)}
                        />
                        <span>{value === "true" ? "Active" : "Desactive"}</span>
                      </label>
                    ) : null}

                    {(valueType === "float" || valueType === "int") ? (
                      <input
                        type="number"
                        step={valueType === "float" ? "0.01" : "1"}
                        value={value}
                        onChange={(event) => onInputChange(row, event)}
                        min={meta.min_value}
                        max={meta.max_value}
                      />
                    ) : null}

                    {!["choice", "bool", "float", "int"].includes(valueType) ? (
                      <input value={value} onChange={(event) => onInputChange(row, event)} />
                    ) : null}
                  </div>

                  <div className="setting-meta">
                    <span>Type: {valueType}</span>
                    {meta.min_value !== undefined && meta.min_value !== null ? (
                      <span>Min: {meta.min_value}</span>
                    ) : null}
                    {meta.max_value !== undefined && meta.max_value !== null ? (
                      <span>Max: {meta.max_value}</span>
                    ) : null}
                    <span>Maj: {new Date(row.updated_at).toLocaleString("fr-FR")}</span>
                  </div>
                </article>
              );
            })}
          </div>
        </section>
      ))}

      {mutation.isError ? <div>Echec de mise a jour du parametre.</div> : null}
    </DashboardShell>
  );
}
