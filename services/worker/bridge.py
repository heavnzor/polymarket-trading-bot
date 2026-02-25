#!/usr/bin/env python3
import json
import logging
import os
import sqlite3
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=os.getenv("BRIDGE_LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] bridge: %(message)s",
)
logger = logging.getLogger("control-plane-bridge")


class ControlPlaneBridge:
    def __init__(self):
        root = Path(__file__).resolve().parents[2]
        self.sqlite_path = Path(os.getenv("WORKER_SQLITE_PATH", str(root / "db" / "polybot.db")))
        self.base_url = os.getenv("CONTROL_PLANE_URL", "http://127.0.0.1:8000/api/v1").rstrip("/")
        self.bridge_token = os.getenv("CONTROL_PLANE_BRIDGE_TOKEN", "change-me-bridge-token")
        self.poll_interval = float(os.getenv("BRIDGE_POLL_INTERVAL_SECONDS", "5"))
        self.trade_window = int(os.getenv("BRIDGE_TRADE_WINDOW", "250"))
        self.trade_recent_sync_count = int(os.getenv("BRIDGE_TRADE_RECENT_SYNC", "5"))
        self.position_window = int(os.getenv("BRIDGE_POSITION_WINDOW", "250"))
        self.positions_interval = float(os.getenv("BRIDGE_POSITIONS_INTERVAL_SECONDS", "10"))
        self.settings_interval = float(os.getenv("BRIDGE_SETTINGS_INTERVAL_SECONDS", "60"))

        state_path = os.getenv("BRIDGE_STATE_PATH")
        if state_path:
            self.state_path = Path(state_path)
        else:
            self.state_path = root / "services" / "worker" / ".bridge_state.json"

        self.state = self._load_state()
        self.session = requests.Session()

        self.db = sqlite3.connect(self.sqlite_path)
        self.db.row_factory = sqlite3.Row

        logger.info("Bridge initialized: sqlite=%s control_plane=%s", self.sqlite_path, self.base_url)

    def _load_state(self) -> dict:
        if self.state_path.exists():
            try:
                return json.loads(self.state_path.read_text())
            except Exception:
                logger.warning("Unable to parse bridge state file, starting fresh")

        return {
            "last_order_event_id": 0,
            "last_command_sync_id": 0,
            "last_trade_id": 0,
            "last_learning_journal_id": 0,
            "last_learning_insight_id": 0,
            "last_learning_proposal_id": 0,
            "last_learning_git_change_id": 0,
            "last_manager_critique_id": 0,
            "last_risk_review_id": 0,
            "last_assessment_id": 0,
            "last_conversation_id": 0,
            "last_file_change_id": 0,
            "last_positions_sync_epoch": 0,
            "last_settings_sync_epoch": 0,
            "sqlite_to_backend": {},
            "last_perf_push_epoch": 0,
        }

    def _save_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(self.state, indent=2, sort_keys=True))

    def _headers(self) -> dict:
        return {
            "Content-Type": "application/json",
            "X-Bridge-Token": self.bridge_token,
        }

    def _post(self, path: str, payload: dict) -> dict | None:
        response = self.session.post(
            f"{self.base_url}{path}",
            json=payload,
            headers=self._headers(),
            timeout=12,
        )
        if response.status_code >= 400:
            logger.error("POST %s failed [%s]: %s", path, response.status_code, response.text)
            return None
        if not response.text:
            return {}
        return response.json()

    def _get(self, path: str) -> dict | list | None:
        response = self.session.get(
            f"{self.base_url}{path}",
            headers=self._headers(),
            timeout=12,
        )
        if response.status_code >= 400:
            logger.error("GET %s failed [%s]: %s", path, response.status_code, response.text)
            return None
        if not response.text:
            return {}
        return response.json()

    def _rows(self, query: str, args: tuple = ()) -> list[dict]:
        cur = self.db.execute(query, args)
        rows = cur.fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _parse_json(value, fallback=None):
        if value is None:
            return fallback
        if isinstance(value, (dict, list)):
            return value
        if isinstance(value, (int, float, bool)):
            return value
        try:
            return json.loads(value)
        except Exception:
            return fallback if fallback is not None else value

    def sync_trades(self) -> None:
        last_trade_id = int(self.state.get("last_trade_id", 0))

        if last_trade_id <= 0:
            rows = self._rows(
                "SELECT * FROM trades ORDER BY id ASC LIMIT ?",
                (self.trade_window,),
            )
        else:
            recent_floor = max(0, last_trade_id - self.trade_recent_sync_count)
            rows = self._rows(
                """
                SELECT *
                FROM trades
                WHERE id > ?
                   OR id > ?
                ORDER BY id ASC
                LIMIT 500
                """,
                (last_trade_id, recent_floor),
            )

        max_id = last_trade_id
        for row in rows:
            self._post("/bridge/trades/upsert/", row)
            max_id = max(max_id, int(row.get("id", 0)))

        self.state["last_trade_id"] = max_id

    def sync_positions(self) -> None:
        now_epoch = time.time()
        last_sync = float(self.state.get("last_positions_sync_epoch", 0))
        if now_epoch - last_sync < self.positions_interval:
            return

        rows = self._rows(
            """
            SELECT *
            FROM positions
            WHERE status='open'
               OR closed_at IS NULL
               OR closed_at >= datetime('now', '-3 day')
            ORDER BY id DESC
            LIMIT ?
            """,
            (self.position_window,),
        )
        for row in reversed(rows):
            self._post("/bridge/positions/upsert/", row)

        self.state["last_positions_sync_epoch"] = now_epoch

    def sync_order_events(self) -> None:
        last_id = int(self.state.get("last_order_event_id", 0))
        rows = self._rows(
            "SELECT * FROM order_events WHERE id > ? ORDER BY id ASC LIMIT 500",
            (last_id,),
        )
        max_id = last_id
        for row in rows:
            payload = dict(row)
            payload["payload"] = self._parse_json(payload.get("payload_json"), {})
            self._post("/bridge/order-events/upsert/", payload)
            max_id = max(max_id, int(row["id"]))

        self.state["last_order_event_id"] = max_id

    def sync_bot_status(self) -> None:
        rows = self._rows("SELECT key, value FROM bot_status")
        payload = {}
        for row in rows:
            payload[row["key"]] = self._parse_json(row["value"], row["value"])
        if payload:
            self._post("/bridge/status/upsert/", {"status": payload})

    def sync_settings(self) -> None:
        now_epoch = time.time()
        last_sync = float(self.state.get("last_settings_sync_epoch", 0))
        if now_epoch - last_sync < self.settings_interval:
            return

        rows = self._rows("SELECT * FROM bot_settings ORDER BY key")
        for row in rows:
            self._post("/bridge/settings/upsert/", row)

        self.state["last_settings_sync_epoch"] = now_epoch

    def sync_sqlite_commands(self) -> None:
        last_id = int(self.state.get("last_command_sync_id", 0))
        rows = self._rows(
            "SELECT * FROM bot_commands WHERE id > ? ORDER BY id ASC LIMIT 500",
            (last_id,),
        )

        max_id = last_id
        for row in rows:
            payload = dict(row)
            payload["payload"] = self._parse_json(payload.get("payload"), {})
            payload["result"] = self._parse_json(payload.get("result"), None)
            self._post("/bridge/commands/upsert/", payload)
            max_id = max(max_id, int(row["id"]))

        self.state["last_command_sync_id"] = max_id

    def sync_commands_from_control_plane(self) -> None:
        payload = self._get("/bridge/commands/pending/?limit=50")
        if not payload:
            return

        sqlite_to_backend = self.state.setdefault("sqlite_to_backend", {})
        backend_ids_already_mapped = {int(v) for v in sqlite_to_backend.values()}

        for command in payload:
            backend_id = int(command["id"])
            if backend_id in backend_ids_already_mapped:
                continue

            cmd_payload = command.get("payload") or {}
            payload_json = json.dumps(cmd_payload)
            now = time.strftime("%Y-%m-%d %H:%M:%S")

            cursor = self.db.execute(
                "INSERT INTO bot_commands (command, payload, status, created_at) VALUES (?, ?, 'pending', ?)",
                (command.get("command", ""), payload_json, now),
            )
            self.db.commit()

            sqlite_id = int(cursor.lastrowid)
            sqlite_to_backend[str(sqlite_id)] = backend_id
            logger.info(
                "Dispatched command backend=%s sqlite=%s cmd=%s",
                backend_id,
                sqlite_id,
                command.get("command"),
            )

    def sync_command_results_to_control_plane(self) -> None:
        sqlite_to_backend = self.state.setdefault("sqlite_to_backend", {})
        if not sqlite_to_backend:
            return

        to_remove = []
        for sqlite_id_str, backend_id in list(sqlite_to_backend.items()):
            sqlite_id = int(sqlite_id_str)
            rows = self._rows(
                "SELECT id, status, result, executed_at FROM bot_commands WHERE id=?",
                (sqlite_id,),
            )
            if not rows:
                to_remove.append(sqlite_id_str)
                continue

            row = rows[0]
            status = row.get("status")
            if status not in {"executed", "failed"}:
                continue

            result = self._parse_json(row.get("result"), None)
            response = self._post(
                "/bridge/commands/result/",
                {
                    "command_id": int(backend_id),
                    "status": status,
                    "result": result,
                },
            )
            if response is not None:
                to_remove.append(sqlite_id_str)
                logger.info(
                    "Reported command result backend=%s sqlite=%s status=%s",
                    backend_id,
                    sqlite_id,
                    status,
                )

        for sqlite_id_str in to_remove:
            sqlite_to_backend.pop(sqlite_id_str, None)

    def sync_performance_snapshot(self) -> None:
        now_epoch = time.time()
        last_push = float(self.state.get("last_perf_push_epoch", 0))
        if now_epoch - last_push < 60:
            return

        rows_total = self._rows("SELECT COUNT(*) AS total FROM performance")
        rows_resolved = self._rows("SELECT COUNT(*) AS resolved FROM performance WHERE market_resolved=1")
        rows_wins = self._rows("SELECT COUNT(*) AS wins FROM performance WHERE market_resolved=1 AND was_correct=1")
        rows_losses = self._rows("SELECT COUNT(*) AS losses FROM performance WHERE market_resolved=1 AND was_correct=0")
        try:
            rows_pnl = self._rows(
                "SELECT COALESCE(SUM(CASE WHEN pnl_net IS NOT NULL THEN pnl_net ELSE pnl_realized END),0) AS pnl FROM performance WHERE market_resolved=1"
            )
        except sqlite3.OperationalError:
            rows_pnl = self._rows(
                "SELECT COALESCE(SUM(pnl_realized),0) AS pnl FROM performance WHERE market_resolved=1"
            )
        rows_wagered = self._rows(
            "SELECT COALESCE(SUM(size_usdc),0) AS wagered FROM performance WHERE market_resolved=1"
        )

        total = float(rows_total[0]["total"]) if rows_total else 0
        resolved = float(rows_resolved[0]["resolved"]) if rows_resolved else 0
        wins = float(rows_wins[0]["wins"]) if rows_wins else 0
        losses = float(rows_losses[0]["losses"]) if rows_losses else 0
        pnl = float(rows_pnl[0]["pnl"]) if rows_pnl else 0
        wagered = float(rows_wagered[0]["wagered"]) if rows_wagered else 0

        graded = wins + losses
        payload = {
            "total_trades": int(total),
            "resolved_trades": int(resolved),
            "pending_resolution": int(total - resolved),
            "wins": int(wins),
            "losses": int(losses),
            "hit_rate": round(wins / graded, 4) if graded else 0,
            "total_pnl": round(pnl, 4),
            "roi_percent": round((pnl / wagered) * 100, 4) if wagered else 0,
        }

        response = self._post(
            "/bridge/performance/upsert/",
            {
                "snapshot_type": "stats",
                "payload": payload,
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            },
        )
        if response is not None:
            self.state["last_perf_push_epoch"] = now_epoch

    def sync_learning_journal(self) -> None:
        last_id = int(self.state.get("last_learning_journal_id", 0))
        try:
            if last_id <= 0:
                rows = self._rows(
                    "SELECT * FROM learning_journal ORDER BY id ASC LIMIT 500",
                )
            else:
                recent_floor = max(0, last_id - 20)
                rows = self._rows(
                    """
                    SELECT * FROM learning_journal
                    WHERE id > ?
                       OR id > ?
                    ORDER BY id ASC LIMIT 500
                    """,
                    (last_id, recent_floor),
                )
        except sqlite3.OperationalError:
            return
        max_id = last_id
        for row in rows:
            self._post("/bridge/learning/journal/upsert/", row)
            max_id = max(max_id, int(row.get("id", 0)))
        self.state["last_learning_journal_id"] = max_id

    def sync_learning_insights(self) -> None:
        last_id = int(self.state.get("last_learning_insight_id", 0))
        try:
            if last_id <= 0:
                rows = self._rows(
                    "SELECT * FROM learning_insights ORDER BY id ASC LIMIT 500",
                )
            else:
                recent_floor = max(0, last_id - 20)
                rows = self._rows(
                    """
                    SELECT * FROM learning_insights
                    WHERE id > ?
                       OR id > ?
                    ORDER BY id ASC LIMIT 500
                    """,
                    (last_id, recent_floor),
                )
        except sqlite3.OperationalError:
            return
        max_id = last_id
        for row in rows:
            self._post("/bridge/learning/insights/upsert/", row)
            max_id = max(max_id, int(row.get("id", 0)))
        self.state["last_learning_insight_id"] = max_id

    def sync_learning_proposals(self) -> None:
        last_id = int(self.state.get("last_learning_proposal_id", 0))
        try:
            if last_id <= 0:
                rows = self._rows(
                    "SELECT * FROM learning_proposals ORDER BY id ASC LIMIT 500",
                )
            else:
                recent_floor = max(0, last_id - 40)
                rows = self._rows(
                    """
                    SELECT * FROM learning_proposals
                    WHERE id > ?
                       OR id > ?
                    ORDER BY id ASC LIMIT 500
                    """,
                    (last_id, recent_floor),
                )
        except sqlite3.OperationalError:
            return
        max_id = last_id
        for row in rows:
            self._post("/bridge/learning/proposals/upsert/", row)
            max_id = max(max_id, int(row.get("id", 0)))
        self.state["last_learning_proposal_id"] = max_id

    def sync_learning_git_changes(self) -> None:
        last_id = int(self.state.get("last_learning_git_change_id", 0))
        try:
            if last_id <= 0:
                rows = self._rows(
                    "SELECT * FROM learning_git_changes ORDER BY id ASC LIMIT 500",
                )
            else:
                recent_floor = max(0, last_id - 20)
                rows = self._rows(
                    """
                    SELECT * FROM learning_git_changes
                    WHERE id > ?
                       OR id > ?
                    ORDER BY id ASC LIMIT 500
                    """,
                    (last_id, recent_floor),
                )
        except sqlite3.OperationalError:
            return
        max_id = last_id
        for row in rows:
            payload = dict(row)
            payload["files_changed"] = self._parse_json(payload.get("files_changed"), [])
            payload["result"] = self._parse_json(payload.get("result"), {})
            self._post("/bridge/learning/git-changes/upsert/", payload)
            max_id = max(max_id, int(row.get("id", 0)))
        self.state["last_learning_git_change_id"] = max_id

    def sync_manager_critiques(self) -> None:
        last_id = int(self.state.get("last_manager_critique_id", 0))
        try:
            if last_id <= 0:
                rows = self._rows(
                    "SELECT * FROM manager_critiques ORDER BY id ASC LIMIT 500",
                )
            else:
                recent_floor = max(0, last_id - 20)
                rows = self._rows(
                    """
                    SELECT * FROM manager_critiques
                    WHERE id > ?
                       OR id > ?
                    ORDER BY id ASC LIMIT 500
                    """,
                    (last_id, recent_floor),
                )
        except sqlite3.OperationalError:
            return
        max_id = last_id
        for row in rows:
            self._post("/bridge/learning/critiques/upsert/", row)
            max_id = max(max_id, int(row.get("id", 0)))
        self.state["last_manager_critique_id"] = max_id

    def sync_risk_reviews(self) -> None:
        last_id = int(self.state.get("last_risk_review_id", 0))
        try:
            if last_id <= 0:
                rows = self._rows(
                    "SELECT * FROM risk_officer_reviews ORDER BY id ASC LIMIT 500",
                )
            else:
                recent_floor = max(0, last_id - 20)
                rows = self._rows(
                    """
                    SELECT * FROM risk_officer_reviews
                    WHERE id > ?
                       OR id > ?
                    ORDER BY id ASC LIMIT 500
                    """,
                    (last_id, recent_floor),
                )
        except sqlite3.OperationalError:
            return
        max_id = last_id
        for row in rows:
            payload = dict(row)
            payload["parameter_recommendations"] = self._parse_json(
                payload.get("parameter_recommendations"), []
            )
            self._post("/bridge/risk-reviews/upsert/", payload)
            max_id = max(max_id, int(row.get("id", 0)))
        self.state["last_risk_review_id"] = max_id

    def sync_strategist_assessments(self) -> None:
        last_id = int(self.state.get("last_assessment_id", 0))
        try:
            if last_id <= 0:
                rows = self._rows(
                    "SELECT * FROM strategist_assessments ORDER BY id ASC LIMIT 500",
                )
            else:
                recent_floor = max(0, last_id - 20)
                rows = self._rows(
                    """
                    SELECT * FROM strategist_assessments
                    WHERE id > ?
                       OR id > ?
                    ORDER BY id ASC LIMIT 500
                    """,
                    (last_id, recent_floor),
                )
        except sqlite3.OperationalError:
            return
        max_id = last_id
        for row in rows:
            self._post("/bridge/strategist/upsert/", row)
            max_id = max(max_id, int(row.get("id", 0)))
        self.state["last_assessment_id"] = max_id

    def sync_conversations(self) -> None:
        last_id = int(self.state.get("last_conversation_id", 0))
        try:
            if last_id <= 0:
                rows = self._rows(
                    "SELECT * FROM conversations ORDER BY id ASC LIMIT 500",
                )
            else:
                recent_floor = max(0, last_id - 20)
                rows = self._rows(
                    """
                    SELECT * FROM conversations
                    WHERE id > ?
                       OR id > ?
                    ORDER BY id ASC LIMIT 500
                    """,
                    (last_id, recent_floor),
                )
        except sqlite3.OperationalError:
            return
        max_id = last_id
        for row in rows:
            self._post("/bridge/chat/upsert/", row)
            max_id = max(max_id, int(row.get("id", 0)))
        self.state["last_conversation_id"] = max_id

    def sync_file_changes(self) -> None:
        last_id = int(self.state.get("last_file_change_id", 0))
        try:
            if last_id <= 0:
                rows = self._rows(
                    "SELECT * FROM file_change_audit ORDER BY id ASC LIMIT 500",
                )
            else:
                recent_floor = max(0, last_id - 20)
                rows = self._rows(
                    """
                    SELECT * FROM file_change_audit
                    WHERE id > ?
                       OR id > ?
                    ORDER BY id ASC LIMIT 500
                    """,
                    (last_id, recent_floor),
                )
        except sqlite3.OperationalError:
            return
        max_id = last_id
        for row in rows:
            self._post("/bridge/audit/upsert/", row)
            max_id = max(max_id, int(row.get("id", 0)))
        self.state["last_file_change_id"] = max_id

    def tick(self) -> None:
        self.sync_trades()
        self.sync_positions()
        self.sync_order_events()
        self.sync_bot_status()
        self.sync_settings()
        self.sync_sqlite_commands()
        self.sync_commands_from_control_plane()
        self.sync_command_results_to_control_plane()
        self.sync_performance_snapshot()
        self.sync_learning_journal()
        self.sync_learning_insights()
        self.sync_learning_proposals()
        self.sync_learning_git_changes()
        self.sync_manager_critiques()
        self.sync_risk_reviews()
        self.sync_strategist_assessments()
        self.sync_conversations()
        self.sync_file_changes()

    def run(self) -> None:
        while True:
            try:
                self.tick()
                self._save_state()
            except Exception:
                logger.exception("Bridge tick failed")
            time.sleep(self.poll_interval)


def main() -> None:
    bridge = ControlPlaneBridge()
    bridge.run()


if __name__ == "__main__":
    main()
