"use client";

import { useEffect, useMemo, useState } from "react";

type FeedEvent = {
  ts: string;
  type: string;
  payload: unknown;
};

function resolveWebsocketUrl(): string {
  const fromEnv = process.env.NEXT_PUBLIC_CONTROL_PLANE_WS_URL?.trim();
  if (fromEnv) {
    if (typeof window !== "undefined" && fromEnv.startsWith("/")) {
      const protocol = window.location.protocol === "https:" ? "wss" : "ws";
      return `${protocol}://${window.location.host}${fromEnv}`;
    }
    return fromEnv;
  }

  if (typeof window !== "undefined") {
    const protocol = window.location.protocol === "https:" ? "wss" : "ws";
    return `${protocol}://${window.location.host}/ws/control-plane/`;
  }

  return "ws://127.0.0.1:8000/ws/control-plane/";
}

export function RealtimeFeed() {
  const [events, setEvents] = useState<FeedEvent[]>([]);
  const [connected, setConnected] = useState(false);

  const websocketUrl = useMemo(() => resolveWebsocketUrl(), []);

  useEffect(() => {
    const ws = new WebSocket(websocketUrl);

    ws.onopen = () => setConnected(true);
    ws.onclose = () => setConnected(false);
    ws.onerror = () => setConnected(false);
    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data.type === "event") {
          setEvents((prev) => [
            {
              ts: data.emitted_at ?? new Date().toISOString(),
              type: data.event_type,
              payload: data.payload,
            },
            ...prev,
          ].slice(0, 14));
        }
      } catch {
        // ignore malformed event
      }
    };

    const ping = setInterval(() => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "ping" }));
      }
    }, 20_000);

    return () => {
      clearInterval(ping);
      ws.close();
    };
  }, [websocketUrl]);

  return (
    <section className="panel">
      <div className="panel-header">
        <h2>Flux temps reel</h2>
        <span className={`badge ${connected ? "live" : "error"}`}>
          {connected ? "Connecte" : "Deconnecte"}
        </span>
      </div>
      <div className="panel-body stack">
        {events.length === 0 ? <div>Aucun evenement pour le moment.</div> : null}
        {events.map((event, idx) => (
          <article key={`${event.ts}-${idx}`} className="kpi">
            <div className="label">{event.type}</div>
            <div className="value" style={{ fontSize: "0.85rem", wordBreak: "break-word" }}>
              {JSON.stringify(event.payload)}
            </div>
            <div className="label" style={{ marginTop: "0.3rem" }}>
              {new Date(event.ts).toLocaleString("fr-FR")}
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}
