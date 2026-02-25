"use client";

import { FormEvent, useState } from "react";

function getBasePath(pathname: string): string {
  const suffix = "/access";
  if (pathname.endsWith(suffix)) {
    return pathname.slice(0, -suffix.length);
  }
  return "";
}

export default function AccessPage() {
  const [password, setPassword] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setIsSubmitting(true);
    setError(null);

    const basePath = getBasePath(window.location.pathname);
    const response = await fetch(`${basePath}/api/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password }),
    });

    setIsSubmitting(false);

    if (!response.ok) {
      setError("Mot de passe invalide.");
      return;
    }

    const params = new URLSearchParams(window.location.search);
    const nextCandidate = params.get("next") || "/overview";
    const nextPath = nextCandidate.startsWith("/") ? nextCandidate : `/${nextCandidate}`;
    window.location.assign(`${basePath}${nextPath}`);
  }

  return (
    <main style={{ minHeight: "100vh", display: "grid", placeItems: "center", padding: "1rem" }}>
      <section
        style={{
          width: "100%",
          maxWidth: 420,
          background: "#0f141f",
          border: "1px solid #263041",
          borderRadius: 14,
          padding: "1.25rem",
          color: "#f4f7ff",
        }}
      >
        <h1 style={{ margin: 0, fontSize: "1.4rem" }}>Acces au dashboard</h1>
        <p style={{ margin: "0.6rem 0 1rem", color: "#9eabc4" }}>
          Entrez le mot de passe pour accéder au control-plane.
        </p>
        <form onSubmit={onSubmit}>
          <input
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            placeholder="Mot de passe"
            autoComplete="current-password"
            required
            style={{
              width: "100%",
              padding: "0.75rem 0.85rem",
              borderRadius: 10,
              border: "1px solid #34435d",
              background: "#0b111b",
              color: "#f4f7ff",
              marginBottom: "0.8rem",
            }}
          />
          <button
            type="submit"
            disabled={isSubmitting}
            style={{
              width: "100%",
              padding: "0.75rem 0.85rem",
              borderRadius: 10,
              border: "1px solid #3f587c",
              background: "#16233a",
              color: "#f4f7ff",
              cursor: isSubmitting ? "wait" : "pointer",
            }}
          >
            {isSubmitting ? "Connexion..." : "Accéder au dashboard"}
          </button>
          {error ? <p style={{ color: "#ff8080", marginTop: "0.8rem" }}>{error}</p> : null}
        </form>
      </section>
    </main>
  );
}
