#!/usr/bin/env python3
import argparse
import json
import sqlite3
from pathlib import Path

import requests


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="One-shot migration SQLite -> Django control-plane")
    parser.add_argument("--sqlite-path", default="db/polybot.db")
    parser.add_argument("--control-plane-url", default="http://127.0.0.1:8000/api/v1")
    parser.add_argument("--bridge-token", required=True)
    return parser.parse_args()


def post(base_url: str, token: str, path: str, payload: dict) -> None:
    response = requests.post(
        f"{base_url.rstrip('/')}{path}",
        json=payload,
        headers={"X-Bridge-Token": token, "Content-Type": "application/json"},
        timeout=20,
    )
    response.raise_for_status()


def parse_json(value, fallback):
    if value is None:
        return fallback
    if isinstance(value, (dict, list, int, float, bool)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return fallback


def migrate_table(conn: sqlite3.Connection, query: str, handler):
    cur = conn.execute(query)
    rows = cur.fetchall()
    for row in rows:
        handler(dict(row))


def main() -> None:
    args = parse_args()
    sqlite_path = Path(args.sqlite_path)
    if not sqlite_path.exists():
        raise FileNotFoundError(f"SQLite DB not found: {sqlite_path}")

    conn = sqlite3.connect(sqlite_path)
    conn.row_factory = sqlite3.Row

    migrate_table(
        conn,
        "SELECT * FROM trades ORDER BY id ASC",
        lambda row: post(args.control_plane_url, args.bridge_token, "/bridge/trades/upsert/", row),
    )
    migrate_table(
        conn,
        "SELECT * FROM positions ORDER BY id ASC",
        lambda row: post(args.control_plane_url, args.bridge_token, "/bridge/positions/upsert/", row),
    )
    migrate_table(
        conn,
        "SELECT * FROM bot_settings ORDER BY key ASC",
        lambda row: post(args.control_plane_url, args.bridge_token, "/bridge/settings/upsert/", row),
    )
    migrate_table(
        conn,
        "SELECT * FROM bot_commands ORDER BY id ASC",
        lambda row: post(
            args.control_plane_url,
            args.bridge_token,
            "/bridge/commands/upsert/",
            {
                **row,
                "payload": parse_json(row.get("payload"), {}),
                "result": parse_json(row.get("result"), None),
            },
        ),
    )
    migrate_table(
        conn,
        "SELECT * FROM order_events ORDER BY id ASC",
        lambda row: post(
            args.control_plane_url,
            args.bridge_token,
            "/bridge/order-events/upsert/",
            {
                **row,
                "payload": parse_json(row.get("payload_json"), {}),
            },
        ),
    )

    status_rows = conn.execute("SELECT key, value FROM bot_status").fetchall()
    payload = {row["key"]: parse_json(row["value"], row["value"]) for row in status_rows}
    if payload:
        post(args.control_plane_url, args.bridge_token, "/bridge/status/upsert/", {"status": payload})

    try:
        perf = conn.execute(
            "SELECT COUNT(*) AS total, "
            "SUM(CASE WHEN market_resolved=1 THEN 1 ELSE 0 END) AS resolved, "
            "SUM(CASE WHEN market_resolved=1 AND was_correct=1 THEN 1 ELSE 0 END) AS wins, "
            "SUM(CASE WHEN market_resolved=1 AND was_correct=0 THEN 1 ELSE 0 END) AS losses, "
            "COALESCE(SUM(CASE WHEN market_resolved=1 THEN COALESCE(pnl_net, pnl_realized) ELSE 0 END), 0) AS pnl, "
            "COALESCE(SUM(CASE WHEN market_resolved=1 THEN size_usdc ELSE 0 END), 0) AS wagered "
            "FROM performance"
        ).fetchone()
    except sqlite3.OperationalError:
        perf = conn.execute(
            "SELECT COUNT(*) AS total, "
            "SUM(CASE WHEN market_resolved=1 THEN 1 ELSE 0 END) AS resolved, "
            "SUM(CASE WHEN market_resolved=1 AND was_correct=1 THEN 1 ELSE 0 END) AS wins, "
            "SUM(CASE WHEN market_resolved=1 AND was_correct=0 THEN 1 ELSE 0 END) AS losses, "
            "COALESCE(SUM(CASE WHEN market_resolved=1 THEN pnl_realized ELSE 0 END), 0) AS pnl, "
            "COALESCE(SUM(CASE WHEN market_resolved=1 THEN size_usdc ELSE 0 END), 0) AS wagered "
            "FROM performance"
        ).fetchone()

    wins = float(perf["wins"] or 0)
    losses = float(perf["losses"] or 0)
    graded = wins + losses
    wagered = float(perf["wagered"] or 0)
    pnl = float(perf["pnl"] or 0)

    snapshot = {
        "total_trades": int(perf["total"] or 0),
        "resolved_trades": int(perf["resolved"] or 0),
        "pending_resolution": int((perf["total"] or 0) - (perf["resolved"] or 0)),
        "wins": int(wins),
        "losses": int(losses),
        "hit_rate": round((wins / graded), 4) if graded else 0,
        "total_pnl": round(pnl, 4),
        "roi_percent": round((pnl / wagered) * 100, 4) if wagered else 0,
    }
    post(
        args.control_plane_url,
        args.bridge_token,
        "/bridge/performance/upsert/",
        {"snapshot_type": "stats", "payload": snapshot},
    )

    print("Migration completed successfully")


if __name__ == "__main__":
    main()
