"""Tests for services/worker/bridge.py — ControlPlaneBridge sync logic."""

import json
import os
import sqlite3
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, Mock, call, patch

import pytest

# Ensure the worker package is importable
WORKER_DIR = Path(__file__).resolve().parents[1]
if str(WORKER_DIR) not in sys.path:
    sys.path.insert(0, str(WORKER_DIR))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_response(status_code: int = 200, json_data=None, text: str | None = None):
    """Create a mock requests.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    if json_data is not None:
        resp.json.return_value = json_data
        resp.text = json.dumps(json_data)
    elif text is not None:
        resp.text = text
        resp.json.side_effect = json.JSONDecodeError("", "", 0)
    else:
        resp.text = ""
        resp.json.return_value = {}
    return resp


def _create_bridge(tmp_path, env_overrides=None):
    """Instantiate a ControlPlaneBridge with a real temp SQLite and mocked session.

    Returns (bridge, mock_session).
    """
    db_path = tmp_path / "polybot.db"
    state_path = tmp_path / ".bridge_state.json"

    env = {
        "WORKER_SQLITE_PATH": str(db_path),
        "CONTROL_PLANE_URL": "http://test-server:8000/api/v1",
        "CONTROL_PLANE_BRIDGE_TOKEN": "test-token-abc",
        "BRIDGE_POLL_INTERVAL_SECONDS": "1",
        "BRIDGE_TRADE_WINDOW": "100",
        "BRIDGE_TRADE_RECENT_SYNC": "3",
        "BRIDGE_POSITION_WINDOW": "100",
        "BRIDGE_POSITIONS_INTERVAL_SECONDS": "10",
        "BRIDGE_SETTINGS_INTERVAL_SECONDS": "60",
        "BRIDGE_STATE_PATH": str(state_path),
    }
    if env_overrides:
        env.update(env_overrides)

    # Create a minimal SQLite DB with required tables
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY,
            market_id TEXT,
            side TEXT,
            price REAL,
            size_usdc REAL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS positions (
            id INTEGER PRIMARY KEY,
            market_id TEXT,
            status TEXT DEFAULT 'open',
            closed_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS order_events (
            id INTEGER PRIMARY KEY,
            event_type TEXT,
            payload_json TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS bot_status (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS bot_settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS bot_commands (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            command TEXT,
            payload TEXT,
            status TEXT DEFAULT 'pending',
            result TEXT,
            executed_at TEXT,
            created_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS performance (
            id INTEGER PRIMARY KEY,
            market_resolved INTEGER DEFAULT 0,
            was_correct INTEGER DEFAULT 0,
            pnl_realized REAL DEFAULT 0,
            pnl_net REAL,
            size_usdc REAL DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS learning_journal (
            id INTEGER PRIMARY KEY,
            entry TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS learning_insights (
            id INTEGER PRIMARY KEY,
            insight TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS learning_proposals (
            id INTEGER PRIMARY KEY,
            proposal TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS learning_git_changes (
            id INTEGER PRIMARY KEY,
            files_changed TEXT,
            result TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS manager_critiques (
            id INTEGER PRIMARY KEY,
            critique TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS risk_officer_reviews (
            id INTEGER PRIMARY KEY,
            review TEXT,
            parameter_recommendations TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS strategist_assessments (
            id INTEGER PRIMARY KEY,
            assessment TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY,
            message TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS file_change_audit (
            id INTEGER PRIMARY KEY,
            file_path TEXT
        )
    """)
    conn.commit()
    conn.close()

    with patch.dict(os.environ, env, clear=False):
        # Patch requests.Session so no real HTTP calls are made
        with patch("requests.Session") as MockSession:
            mock_session = MagicMock()
            MockSession.return_value = mock_session
            bridge = __import__("bridge").ControlPlaneBridge()
            # Replace session with our mock
            bridge.session = mock_session

    return bridge, mock_session


# =========================================================================
# Initialization
# =========================================================================

class TestBridgeInit:

    def test_init_loads_env_vars(self, tmp_path):
        bridge, _ = _create_bridge(tmp_path)

        assert str(bridge.sqlite_path) == str(tmp_path / "polybot.db")
        assert bridge.base_url == "http://test-server:8000/api/v1"
        assert bridge.bridge_token == "test-token-abc"
        assert bridge.poll_interval == 1.0
        assert bridge.trade_window == 100
        assert bridge.trade_recent_sync_count == 3
        assert bridge.position_window == 100
        assert bridge.positions_interval == 10.0
        assert bridge.settings_interval == 60.0

    def test_init_default_state(self, tmp_path):
        bridge, _ = _create_bridge(tmp_path)

        assert bridge.state["last_trade_id"] == 0
        assert bridge.state["last_order_event_id"] == 0
        assert bridge.state["last_command_sync_id"] == 0
        assert bridge.state["last_learning_journal_id"] == 0
        assert bridge.state["last_learning_insight_id"] == 0
        assert bridge.state["last_learning_proposal_id"] == 0
        assert bridge.state["last_learning_git_change_id"] == 0
        assert bridge.state["last_manager_critique_id"] == 0
        assert bridge.state["last_risk_review_id"] == 0
        assert bridge.state["last_assessment_id"] == 0
        assert bridge.state["last_conversation_id"] == 0
        assert bridge.state["last_file_change_id"] == 0
        assert bridge.state["last_positions_sync_epoch"] == 0
        assert bridge.state["last_settings_sync_epoch"] == 0
        assert bridge.state["sqlite_to_backend"] == {}
        assert bridge.state["last_perf_push_epoch"] == 0

    def test_init_loads_existing_state(self, tmp_path):
        state_path = tmp_path / ".bridge_state.json"
        state_data = {
            "last_trade_id": 42,
            "last_order_event_id": 10,
            "last_command_sync_id": 0,
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
        state_path.write_text(json.dumps(state_data))

        bridge, _ = _create_bridge(tmp_path)
        assert bridge.state["last_trade_id"] == 42
        assert bridge.state["last_order_event_id"] == 10

    def test_init_corrupted_state_file(self, tmp_path):
        state_path = tmp_path / ".bridge_state.json"
        state_path.write_text("NOT VALID JSON {{{")

        bridge, _ = _create_bridge(tmp_path)
        # Should fall back to default state
        assert bridge.state["last_trade_id"] == 0

    def test_init_default_urls_without_env(self, tmp_path):
        """When CONTROL_PLANE_URL is not set, fallback to localhost."""
        db_path = tmp_path / "polybot.db"
        sqlite3.connect(str(db_path)).close()  # Create empty DB
        state_path = tmp_path / ".bridge_state_default.json"

        env = {
            "WORKER_SQLITE_PATH": str(db_path),
            "BRIDGE_STATE_PATH": str(state_path),
        }
        cleaned = os.environ.copy()
        cleaned.pop("CONTROL_PLANE_URL", None)
        cleaned.pop("CONTROL_PLANE_BRIDGE_TOKEN", None)
        cleaned.update(env)

        with patch.dict(os.environ, cleaned, clear=True):
            with patch("requests.Session"):
                with patch("dotenv.load_dotenv", return_value=None):
                    bridge_mod = __import__("bridge")
                    import importlib
                    importlib.reload(bridge_mod)
                    bridge = bridge_mod.ControlPlaneBridge()

        assert bridge.base_url == "http://127.0.0.1:8000/api/v1"
        assert bridge.bridge_token == "change-me-bridge-token"


# =========================================================================
# _parse_json static method
# =========================================================================

class TestParseJson:

    def test_valid_json_string(self):
        from bridge import ControlPlaneBridge
        result = ControlPlaneBridge._parse_json('{"key": "value"}')
        assert result == {"key": "value"}

    def test_valid_json_array_string(self):
        from bridge import ControlPlaneBridge
        result = ControlPlaneBridge._parse_json('[1, 2, 3]')
        assert result == [1, 2, 3]

    def test_invalid_json_with_fallback(self):
        from bridge import ControlPlaneBridge
        result = ControlPlaneBridge._parse_json("not json", fallback={"default": True})
        assert result == {"default": True}

    def test_invalid_json_no_fallback(self):
        from bridge import ControlPlaneBridge
        # When fallback is None and value is not valid JSON, returns the raw value
        result = ControlPlaneBridge._parse_json("not json")
        assert result == "not json"

    def test_none_value_returns_fallback(self):
        from bridge import ControlPlaneBridge
        result = ControlPlaneBridge._parse_json(None, fallback=[])
        assert result == []

    def test_none_value_no_fallback(self):
        from bridge import ControlPlaneBridge
        result = ControlPlaneBridge._parse_json(None)
        assert result is None

    def test_dict_passthrough(self):
        from bridge import ControlPlaneBridge
        data = {"already": "parsed"}
        result = ControlPlaneBridge._parse_json(data)
        assert result is data

    def test_list_passthrough(self):
        from bridge import ControlPlaneBridge
        data = [1, 2, 3]
        result = ControlPlaneBridge._parse_json(data)
        assert result is data

    def test_int_passthrough(self):
        from bridge import ControlPlaneBridge
        result = ControlPlaneBridge._parse_json(42)
        assert result == 42

    def test_float_passthrough(self):
        from bridge import ControlPlaneBridge
        result = ControlPlaneBridge._parse_json(3.14)
        assert result == 3.14

    def test_bool_passthrough(self):
        from bridge import ControlPlaneBridge
        result = ControlPlaneBridge._parse_json(True)
        assert result is True

    def test_nested_json(self):
        from bridge import ControlPlaneBridge
        nested = '{"a": {"b": [1, 2]}, "c": true}'
        result = ControlPlaneBridge._parse_json(nested)
        assert result == {"a": {"b": [1, 2]}, "c": True}


# =========================================================================
# _headers
# =========================================================================

class TestHeaders:

    def test_headers_contain_token_and_content_type(self, tmp_path):
        bridge, _ = _create_bridge(tmp_path)
        headers = bridge._headers()

        assert headers == {
            "Content-Type": "application/json",
            "X-Bridge-Token": "test-token-abc",
        }

    def test_headers_change_with_token(self, tmp_path):
        bridge, _ = _create_bridge(tmp_path)
        bridge.bridge_token = "new-secret-token"
        headers = bridge._headers()
        assert headers["X-Bridge-Token"] == "new-secret-token"


# =========================================================================
# _post
# =========================================================================

class TestPost:

    def test_post_success(self, tmp_path):
        bridge, mock_session = _create_bridge(tmp_path)
        mock_session.post.return_value = _make_response(200, {"ok": True})

        result = bridge._post("/bridge/test/", {"foo": "bar"})

        assert result == {"ok": True}
        mock_session.post.assert_called_once_with(
            "http://test-server:8000/api/v1/bridge/test/",
            json={"foo": "bar"},
            headers=bridge._headers(),
            timeout=12,
        )

    def test_post_empty_response_body(self, tmp_path):
        bridge, mock_session = _create_bridge(tmp_path)
        mock_session.post.return_value = _make_response(200, text="")
        # Override .text to empty string
        mock_session.post.return_value.text = ""

        result = bridge._post("/bridge/test/", {})
        assert result == {}

    def test_post_http_error(self, tmp_path):
        bridge, mock_session = _create_bridge(tmp_path)
        resp = _make_response(500, text="Internal Server Error")
        resp.status_code = 500
        resp.text = "Internal Server Error"
        mock_session.post.return_value = resp

        result = bridge._post("/bridge/test/", {"data": 1})
        assert result is None

    def test_post_http_400(self, tmp_path):
        bridge, mock_session = _create_bridge(tmp_path)
        resp = _make_response(400, text="Bad Request")
        resp.status_code = 400
        resp.text = "Bad Request"
        mock_session.post.return_value = resp

        result = bridge._post("/bridge/test/", {"data": 1})
        assert result is None

    def test_post_timeout(self, tmp_path):
        bridge, mock_session = _create_bridge(tmp_path)
        import requests as req
        mock_session.post.side_effect = req.exceptions.Timeout("Connection timed out")

        with pytest.raises(req.exceptions.Timeout):
            bridge._post("/bridge/test/", {})

    def test_post_connection_error(self, tmp_path):
        bridge, mock_session = _create_bridge(tmp_path)
        import requests as req
        mock_session.post.side_effect = req.exceptions.ConnectionError("Refused")

        with pytest.raises(req.exceptions.ConnectionError):
            bridge._post("/bridge/test/", {})


# =========================================================================
# _get
# =========================================================================

class TestGet:

    def test_get_success_dict(self, tmp_path):
        bridge, mock_session = _create_bridge(tmp_path)
        mock_session.get.return_value = _make_response(200, {"result": "ok"})

        result = bridge._get("/bridge/status/")
        assert result == {"result": "ok"}
        mock_session.get.assert_called_once_with(
            "http://test-server:8000/api/v1/bridge/status/",
            headers=bridge._headers(),
            timeout=12,
        )

    def test_get_success_list(self, tmp_path):
        bridge, mock_session = _create_bridge(tmp_path)
        mock_session.get.return_value = _make_response(200, [{"id": 1}, {"id": 2}])

        result = bridge._get("/bridge/commands/pending/")
        assert result == [{"id": 1}, {"id": 2}]

    def test_get_empty_body(self, tmp_path):
        bridge, mock_session = _create_bridge(tmp_path)
        resp = _make_response(200)
        resp.text = ""
        mock_session.get.return_value = resp

        result = bridge._get("/bridge/test/")
        assert result == {}

    def test_get_http_error(self, tmp_path):
        bridge, mock_session = _create_bridge(tmp_path)
        resp = _make_response(404, text="Not Found")
        resp.status_code = 404
        resp.text = "Not Found"
        mock_session.get.return_value = resp

        result = bridge._get("/bridge/nonexistent/")
        assert result is None

    def test_get_timeout(self, tmp_path):
        bridge, mock_session = _create_bridge(tmp_path)
        import requests as req
        mock_session.get.side_effect = req.exceptions.Timeout("Timed out")

        with pytest.raises(req.exceptions.Timeout):
            bridge._get("/bridge/test/")


# =========================================================================
# _rows — SQLite read helper
# =========================================================================

class TestRows:

    def test_rows_empty_table(self, tmp_path):
        bridge, _ = _create_bridge(tmp_path)
        rows = bridge._rows("SELECT * FROM trades")
        assert rows == []

    def test_rows_with_data(self, tmp_path):
        bridge, _ = _create_bridge(tmp_path)
        bridge.db.execute(
            "INSERT INTO trades (id, market_id, side, price, size_usdc) VALUES (1, 'mkt1', 'BUY', 0.55, 5.0)"
        )
        bridge.db.commit()

        rows = bridge._rows("SELECT * FROM trades WHERE id > ?", (0,))
        assert len(rows) == 1
        assert rows[0]["market_id"] == "mkt1"
        assert rows[0]["side"] == "BUY"
        assert rows[0]["price"] == 0.55

    def test_rows_returns_dicts(self, tmp_path):
        bridge, _ = _create_bridge(tmp_path)
        bridge.db.execute(
            "INSERT INTO trades (id, market_id, side, price, size_usdc) VALUES (1, 'mkt1', 'BUY', 0.55, 5.0)"
        )
        bridge.db.commit()

        rows = bridge._rows("SELECT * FROM trades")
        assert isinstance(rows[0], dict)
        assert "id" in rows[0]
        assert "market_id" in rows[0]


# =========================================================================
# sync_trades
# =========================================================================

class TestSyncTrades:

    def test_sync_trades_initial_batch(self, tmp_path):
        bridge, mock_session = _create_bridge(tmp_path)
        mock_session.post.return_value = _make_response(200, {"ok": True})

        # Insert some trades
        for i in range(1, 4):
            bridge.db.execute(
                "INSERT INTO trades (id, market_id, side, price, size_usdc) VALUES (?, ?, 'BUY', 0.5, 5.0)",
                (i, f"mkt{i}"),
            )
        bridge.db.commit()

        bridge.sync_trades()

        assert mock_session.post.call_count == 3
        assert bridge.state["last_trade_id"] == 3

    def test_sync_trades_incremental(self, tmp_path):
        bridge, mock_session = _create_bridge(tmp_path)
        mock_session.post.return_value = _make_response(200, {"ok": True})

        # Pre-set state as if we already synced up to id 2
        bridge.state["last_trade_id"] = 2

        for i in range(1, 6):
            bridge.db.execute(
                "INSERT INTO trades (id, market_id, side, price, size_usdc) VALUES (?, ?, 'BUY', 0.5, 5.0)",
                (i, f"mkt{i}"),
            )
        bridge.db.commit()

        bridge.sync_trades()

        # Should sync trades with id > 2 plus recent ones (id > max(0, 2-3) = 0, so all)
        assert bridge.state["last_trade_id"] == 5

    def test_sync_trades_empty(self, tmp_path):
        bridge, mock_session = _create_bridge(tmp_path)
        bridge.sync_trades()

        mock_session.post.assert_not_called()
        assert bridge.state["last_trade_id"] == 0


# =========================================================================
# sync_positions
# =========================================================================

class TestSyncPositions:

    def test_sync_positions_skips_if_too_soon(self, tmp_path):
        bridge, mock_session = _create_bridge(tmp_path)
        bridge.state["last_positions_sync_epoch"] = time.time()  # just now

        bridge.sync_positions()
        mock_session.post.assert_not_called()

    def test_sync_positions_runs_after_interval(self, tmp_path):
        bridge, mock_session = _create_bridge(tmp_path)
        mock_session.post.return_value = _make_response(200, {"ok": True})
        bridge.state["last_positions_sync_epoch"] = 0  # long ago

        bridge.db.execute(
            "INSERT INTO positions (id, market_id, status) VALUES (1, 'mkt1', 'open')"
        )
        bridge.db.commit()

        bridge.sync_positions()

        assert mock_session.post.call_count == 1
        assert bridge.state["last_positions_sync_epoch"] > 0


# =========================================================================
# sync_bot_status
# =========================================================================

class TestSyncBotStatus:

    def test_sync_bot_status_empty(self, tmp_path):
        bridge, mock_session = _create_bridge(tmp_path)
        bridge.sync_bot_status()
        mock_session.post.assert_not_called()

    def test_sync_bot_status_with_data(self, tmp_path):
        bridge, mock_session = _create_bridge(tmp_path)
        mock_session.post.return_value = _make_response(200, {"ok": True})

        bridge.db.execute("INSERT INTO bot_status (key, value) VALUES ('cycle', '42')")
        bridge.db.execute("INSERT INTO bot_status (key, value) VALUES ('running', 'true')")
        bridge.db.commit()

        bridge.sync_bot_status()

        mock_session.post.assert_called_once()
        posted_payload = mock_session.post.call_args[1]["json"]
        assert "status" in posted_payload
        assert posted_payload["status"]["cycle"] == 42
        assert posted_payload["status"]["running"] is True

    def test_sync_bot_status_with_json_value(self, tmp_path):
        bridge, mock_session = _create_bridge(tmp_path)
        mock_session.post.return_value = _make_response(200, {"ok": True})

        bridge.db.execute(
            "INSERT INTO bot_status (key, value) VALUES ('positions', ?)",
            (json.dumps(["pos1", "pos2"]),),
        )
        bridge.db.commit()

        bridge.sync_bot_status()

        posted_payload = mock_session.post.call_args[1]["json"]
        assert posted_payload["status"]["positions"] == ["pos1", "pos2"]


# =========================================================================
# sync_settings
# =========================================================================

class TestSyncSettings:

    def test_sync_settings_skips_if_too_soon(self, tmp_path):
        bridge, mock_session = _create_bridge(tmp_path)
        bridge.state["last_settings_sync_epoch"] = time.time()

        bridge.sync_settings()
        mock_session.post.assert_not_called()

    def test_sync_settings_runs_after_interval(self, tmp_path):
        bridge, mock_session = _create_bridge(tmp_path)
        mock_session.post.return_value = _make_response(200, {"ok": True})
        bridge.state["last_settings_sync_epoch"] = 0

        bridge.db.execute("INSERT INTO bot_settings (key, value) VALUES ('heartbeat_enabled', 'true')")
        bridge.db.commit()

        bridge.sync_settings()

        assert mock_session.post.call_count == 1
        assert bridge.state["last_settings_sync_epoch"] > 0


# =========================================================================
# sync_order_events
# =========================================================================

class TestSyncOrderEvents:

    def test_sync_order_events_empty(self, tmp_path):
        bridge, mock_session = _create_bridge(tmp_path)
        bridge.sync_order_events()
        mock_session.post.assert_not_called()

    def test_sync_order_events_with_data(self, tmp_path):
        bridge, mock_session = _create_bridge(tmp_path)
        mock_session.post.return_value = _make_response(200, {"ok": True})

        bridge.db.execute(
            "INSERT INTO order_events (id, event_type, payload_json) VALUES (1, 'FILL', ?)",
            (json.dumps({"qty": 10}),),
        )
        bridge.db.commit()

        bridge.sync_order_events()

        assert mock_session.post.call_count == 1
        assert bridge.state["last_order_event_id"] == 1

        # Verify payload_json is parsed into 'payload' key
        posted = mock_session.post.call_args[1]["json"]
        assert posted["payload"] == {"qty": 10}


# =========================================================================
# sync_sqlite_commands
# =========================================================================

class TestSyncSqliteCommands:

    def test_sync_sqlite_commands_empty(self, tmp_path):
        bridge, mock_session = _create_bridge(tmp_path)
        bridge.sync_sqlite_commands()
        mock_session.post.assert_not_called()

    def test_sync_sqlite_commands_with_data(self, tmp_path):
        bridge, mock_session = _create_bridge(tmp_path)
        mock_session.post.return_value = _make_response(200, {"ok": True})

        bridge.db.execute(
            "INSERT INTO bot_commands (command, payload, status, created_at) VALUES (?, ?, 'executed', '2026-01-01')",
            ("pause", json.dumps({"reason": "test"})),
        )
        bridge.db.commit()

        bridge.sync_sqlite_commands()

        assert mock_session.post.call_count == 1
        assert bridge.state["last_command_sync_id"] == 1


# =========================================================================
# sync_commands_from_control_plane
# =========================================================================

class TestSyncCommandsFromControlPlane:

    def test_no_pending_commands(self, tmp_path):
        bridge, mock_session = _create_bridge(tmp_path)
        mock_session.get.return_value = _make_response(200, [])

        bridge.sync_commands_from_control_plane()

        # Empty list is falsy, so no DB insert should happen
        rows = bridge._rows("SELECT * FROM bot_commands")
        assert len(rows) == 0

    def test_dispatches_pending_commands(self, tmp_path):
        bridge, mock_session = _create_bridge(tmp_path)
        commands = [
            {"id": 100, "command": "pause", "payload": {"duration": 60}},
            {"id": 101, "command": "resume", "payload": {}},
        ]
        mock_session.get.return_value = _make_response(200, commands)

        bridge.sync_commands_from_control_plane()

        rows = bridge._rows("SELECT * FROM bot_commands ORDER BY id")
        assert len(rows) == 2
        assert rows[0]["command"] == "pause"
        assert rows[1]["command"] == "resume"

        # Check sqlite_to_backend mapping
        mapping = bridge.state["sqlite_to_backend"]
        assert len(mapping) == 2

    def test_skips_already_mapped_commands(self, tmp_path):
        bridge, mock_session = _create_bridge(tmp_path)
        # Pre-populate mapping
        bridge.state["sqlite_to_backend"] = {"1": 100}

        commands = [{"id": 100, "command": "pause", "payload": {}}]
        mock_session.get.return_value = _make_response(200, commands)

        bridge.sync_commands_from_control_plane()

        rows = bridge._rows("SELECT * FROM bot_commands")
        assert len(rows) == 0  # Should not insert again

    def test_handles_get_failure(self, tmp_path):
        bridge, mock_session = _create_bridge(tmp_path)
        mock_session.get.return_value = _make_response(500, text="Error")
        mock_session.get.return_value.status_code = 500
        mock_session.get.return_value.text = "Error"

        # _get returns None on error, sync should handle gracefully
        bridge.sync_commands_from_control_plane()

        rows = bridge._rows("SELECT * FROM bot_commands")
        assert len(rows) == 0


# =========================================================================
# sync_command_results_to_control_plane
# =========================================================================

class TestSyncCommandResults:

    def test_no_mapping_noop(self, tmp_path):
        bridge, mock_session = _create_bridge(tmp_path)
        bridge.state["sqlite_to_backend"] = {}

        bridge.sync_command_results_to_control_plane()
        mock_session.post.assert_not_called()

    def test_reports_executed_command(self, tmp_path):
        bridge, mock_session = _create_bridge(tmp_path)
        mock_session.post.return_value = _make_response(200, {"ok": True})

        # Insert an executed command
        bridge.db.execute(
            "INSERT INTO bot_commands (id, command, payload, status, result, executed_at, created_at) "
            "VALUES (1, 'pause', '{}', 'executed', '{\"success\": true}', '2026-01-01', '2026-01-01')"
        )
        bridge.db.commit()
        bridge.state["sqlite_to_backend"] = {"1": 200}

        bridge.sync_command_results_to_control_plane()

        mock_session.post.assert_called_once()
        posted = mock_session.post.call_args[1]["json"]
        assert posted["command_id"] == 200
        assert posted["status"] == "executed"
        assert posted["result"] == {"success": True}

        # Mapping should be cleared
        assert "1" not in bridge.state["sqlite_to_backend"]

    def test_skips_pending_command(self, tmp_path):
        bridge, mock_session = _create_bridge(tmp_path)

        bridge.db.execute(
            "INSERT INTO bot_commands (id, command, payload, status, created_at) "
            "VALUES (1, 'pause', '{}', 'pending', '2026-01-01')"
        )
        bridge.db.commit()
        bridge.state["sqlite_to_backend"] = {"1": 200}

        bridge.sync_command_results_to_control_plane()
        mock_session.post.assert_not_called()

        # Mapping should still be there since command is still pending
        assert "1" in bridge.state["sqlite_to_backend"]

    def test_removes_missing_sqlite_row(self, tmp_path):
        bridge, mock_session = _create_bridge(tmp_path)

        # Map to a nonexistent SQLite row
        bridge.state["sqlite_to_backend"] = {"999": 200}

        bridge.sync_command_results_to_control_plane()

        assert "999" not in bridge.state["sqlite_to_backend"]


# =========================================================================
# sync_performance_snapshot
# =========================================================================

class TestSyncPerformanceSnapshot:

    def test_skips_if_too_recent(self, tmp_path):
        bridge, mock_session = _create_bridge(tmp_path)
        bridge.state["last_perf_push_epoch"] = time.time()

        bridge.sync_performance_snapshot()
        mock_session.post.assert_not_called()

    def test_pushes_snapshot(self, tmp_path):
        bridge, mock_session = _create_bridge(tmp_path)
        mock_session.post.return_value = _make_response(200, {"ok": True})
        bridge.state["last_perf_push_epoch"] = 0

        # Insert some performance data
        bridge.db.execute(
            "INSERT INTO performance (id, market_resolved, was_correct, pnl_realized, pnl_net, size_usdc) "
            "VALUES (1, 1, 1, 5.0, 4.5, 10.0)"
        )
        bridge.db.execute(
            "INSERT INTO performance (id, market_resolved, was_correct, pnl_realized, pnl_net, size_usdc) "
            "VALUES (2, 1, 0, -3.0, -3.5, 8.0)"
        )
        bridge.db.commit()

        bridge.sync_performance_snapshot()

        mock_session.post.assert_called_once()
        posted = mock_session.post.call_args[1]["json"]
        payload = posted["payload"]
        assert payload["total_trades"] == 2
        assert payload["resolved_trades"] == 2
        assert payload["wins"] == 1
        assert payload["losses"] == 1
        assert payload["hit_rate"] == 0.5
        assert posted["snapshot_type"] == "stats"

    def test_empty_performance_table(self, tmp_path):
        bridge, mock_session = _create_bridge(tmp_path)
        mock_session.post.return_value = _make_response(200, {"ok": True})
        bridge.state["last_perf_push_epoch"] = 0

        bridge.sync_performance_snapshot()

        posted = mock_session.post.call_args[1]["json"]
        payload = posted["payload"]
        assert payload["total_trades"] == 0
        assert payload["hit_rate"] == 0
        assert payload["total_pnl"] == 0


# =========================================================================
# sync_learning_journal / insights / proposals / git_changes
# =========================================================================

class TestSyncLearningTables:

    def test_sync_learning_journal(self, tmp_path):
        bridge, mock_session = _create_bridge(tmp_path)
        mock_session.post.return_value = _make_response(200, {"ok": True})

        bridge.db.execute("INSERT INTO learning_journal (id, entry) VALUES (1, 'lesson 1')")
        bridge.db.commit()

        bridge.sync_learning_journal()
        assert mock_session.post.call_count == 1
        assert bridge.state["last_learning_journal_id"] == 1

    def test_sync_learning_insights(self, tmp_path):
        bridge, mock_session = _create_bridge(tmp_path)
        mock_session.post.return_value = _make_response(200, {"ok": True})

        bridge.db.execute("INSERT INTO learning_insights (id, insight) VALUES (1, 'insight 1')")
        bridge.db.commit()

        bridge.sync_learning_insights()
        assert mock_session.post.call_count == 1
        assert bridge.state["last_learning_insight_id"] == 1

    def test_sync_learning_proposals(self, tmp_path):
        bridge, mock_session = _create_bridge(tmp_path)
        mock_session.post.return_value = _make_response(200, {"ok": True})

        bridge.db.execute("INSERT INTO learning_proposals (id, proposal) VALUES (1, 'proposal 1')")
        bridge.db.commit()

        bridge.sync_learning_proposals()
        assert mock_session.post.call_count == 1
        assert bridge.state["last_learning_proposal_id"] == 1

    def test_sync_learning_git_changes(self, tmp_path):
        bridge, mock_session = _create_bridge(tmp_path)
        mock_session.post.return_value = _make_response(200, {"ok": True})

        bridge.db.execute(
            "INSERT INTO learning_git_changes (id, files_changed, result) VALUES (1, ?, ?)",
            (json.dumps(["file1.py"]), json.dumps({"ok": True})),
        )
        bridge.db.commit()

        bridge.sync_learning_git_changes()
        assert mock_session.post.call_count == 1
        assert bridge.state["last_learning_git_change_id"] == 1

        # Verify JSON fields are parsed
        posted = mock_session.post.call_args[1]["json"]
        assert posted["files_changed"] == ["file1.py"]
        assert posted["result"] == {"ok": True}

    def test_sync_learning_journal_incremental(self, tmp_path):
        bridge, mock_session = _create_bridge(tmp_path)
        mock_session.post.return_value = _make_response(200, {"ok": True})
        bridge.state["last_learning_journal_id"] = 1

        bridge.db.execute("INSERT INTO learning_journal (id, entry) VALUES (1, 'old')")
        bridge.db.execute("INSERT INTO learning_journal (id, entry) VALUES (2, 'new')")
        bridge.db.commit()

        bridge.sync_learning_journal()
        assert bridge.state["last_learning_journal_id"] == 2


# =========================================================================
# sync_manager_critiques / risk_reviews / strategist_assessments
# =========================================================================

class TestSyncAgentTables:

    def test_sync_manager_critiques(self, tmp_path):
        bridge, mock_session = _create_bridge(tmp_path)
        mock_session.post.return_value = _make_response(200, {"ok": True})

        bridge.db.execute("INSERT INTO manager_critiques (id, critique) VALUES (1, 'critique 1')")
        bridge.db.commit()

        bridge.sync_manager_critiques()
        assert mock_session.post.call_count == 1
        assert bridge.state["last_manager_critique_id"] == 1

    def test_sync_risk_reviews(self, tmp_path):
        bridge, mock_session = _create_bridge(tmp_path)
        mock_session.post.return_value = _make_response(200, {"ok": True})

        bridge.db.execute(
            "INSERT INTO risk_officer_reviews (id, review, parameter_recommendations) VALUES (1, 'review 1', ?)",
            (json.dumps([{"param": "max_budget", "value": 200}]),),
        )
        bridge.db.commit()

        bridge.sync_risk_reviews()
        assert mock_session.post.call_count == 1
        assert bridge.state["last_risk_review_id"] == 1

        # Verify parameter_recommendations is parsed
        posted = mock_session.post.call_args[1]["json"]
        assert posted["parameter_recommendations"] == [{"param": "max_budget", "value": 200}]

    def test_sync_strategist_assessments(self, tmp_path):
        bridge, mock_session = _create_bridge(tmp_path)
        mock_session.post.return_value = _make_response(200, {"ok": True})

        bridge.db.execute("INSERT INTO strategist_assessments (id, assessment) VALUES (1, 'assess 1')")
        bridge.db.commit()

        bridge.sync_strategist_assessments()
        assert mock_session.post.call_count == 1
        assert bridge.state["last_assessment_id"] == 1


# =========================================================================
# sync_conversations / file_change_audit
# =========================================================================

class TestSyncMiscTables:

    def test_sync_conversations(self, tmp_path):
        bridge, mock_session = _create_bridge(tmp_path)
        mock_session.post.return_value = _make_response(200, {"ok": True})

        bridge.db.execute("INSERT INTO conversations (id, message) VALUES (1, 'hello')")
        bridge.db.commit()

        bridge.sync_conversations()
        assert mock_session.post.call_count == 1
        assert bridge.state["last_conversation_id"] == 1

    def test_sync_file_changes(self, tmp_path):
        bridge, mock_session = _create_bridge(tmp_path)
        mock_session.post.return_value = _make_response(200, {"ok": True})

        bridge.db.execute("INSERT INTO file_change_audit (id, file_path) VALUES (1, '/tmp/foo.py')")
        bridge.db.commit()

        bridge.sync_file_changes()
        assert mock_session.post.call_count == 1
        assert bridge.state["last_file_change_id"] == 1


# =========================================================================
# tick — calls all sync methods
# =========================================================================

class TestTick:

    def test_tick_calls_all_sync_methods(self, tmp_path):
        bridge, mock_session = _create_bridge(tmp_path)

        expected_methods = [
            "sync_trades",
            "sync_positions",
            "sync_order_events",
            "sync_bot_status",
            "sync_settings",
            "sync_sqlite_commands",
            "sync_commands_from_control_plane",
            "sync_command_results_to_control_plane",
            "sync_performance_snapshot",
            "sync_learning_journal",
            "sync_learning_insights",
            "sync_learning_proposals",
            "sync_learning_git_changes",
            "sync_manager_critiques",
            "sync_risk_reviews",
            "sync_strategist_assessments",
            "sync_conversations",
            "sync_file_changes",
        ]

        mocks = {}
        for method_name in expected_methods:
            mock_method = MagicMock()
            setattr(bridge, method_name, mock_method)
            mocks[method_name] = mock_method

        bridge.tick()

        for method_name, mock_method in mocks.items():
            mock_method.assert_called_once(), f"{method_name} was not called exactly once"

    def test_tick_calls_methods_in_order(self, tmp_path):
        bridge, mock_session = _create_bridge(tmp_path)

        call_order = []

        method_names = [
            "sync_trades",
            "sync_positions",
            "sync_order_events",
            "sync_bot_status",
            "sync_settings",
            "sync_sqlite_commands",
            "sync_commands_from_control_plane",
            "sync_command_results_to_control_plane",
            "sync_performance_snapshot",
            "sync_learning_journal",
            "sync_learning_insights",
            "sync_learning_proposals",
            "sync_learning_git_changes",
            "sync_manager_critiques",
            "sync_risk_reviews",
            "sync_strategist_assessments",
            "sync_conversations",
            "sync_file_changes",
        ]

        for name in method_names:
            mock_method = MagicMock(side_effect=lambda n=name: call_order.append(n))
            setattr(bridge, name, mock_method)

        bridge.tick()

        assert call_order == method_names


# =========================================================================
# Error handling in sync methods
# =========================================================================

class TestErrorHandling:

    def test_sync_trades_handles_post_failure(self, tmp_path):
        """sync_trades continues even when _post returns None."""
        bridge, mock_session = _create_bridge(tmp_path)
        mock_session.post.return_value = _make_response(500, text="Error")
        mock_session.post.return_value.status_code = 500
        mock_session.post.return_value.text = "Error"

        bridge.db.execute(
            "INSERT INTO trades (id, market_id, side, price, size_usdc) VALUES (1, 'mkt1', 'BUY', 0.5, 5.0)"
        )
        bridge.db.commit()

        # Should not raise
        bridge.sync_trades()
        # State still updates to track highest seen id
        assert bridge.state["last_trade_id"] == 1

    def test_sync_learning_journal_handles_missing_table(self, tmp_path):
        """If the table does not exist, OperationalError is caught."""
        bridge, mock_session = _create_bridge(tmp_path)

        # Drop the table
        bridge.db.execute("DROP TABLE learning_journal")
        bridge.db.commit()

        # Should not raise
        bridge.sync_learning_journal()

    def test_sync_learning_insights_handles_missing_table(self, tmp_path):
        bridge, mock_session = _create_bridge(tmp_path)
        bridge.db.execute("DROP TABLE learning_insights")
        bridge.db.commit()
        bridge.sync_learning_insights()  # Should not raise

    def test_sync_learning_proposals_handles_missing_table(self, tmp_path):
        bridge, mock_session = _create_bridge(tmp_path)
        bridge.db.execute("DROP TABLE learning_proposals")
        bridge.db.commit()
        bridge.sync_learning_proposals()  # Should not raise

    def test_sync_learning_git_changes_handles_missing_table(self, tmp_path):
        bridge, mock_session = _create_bridge(tmp_path)
        bridge.db.execute("DROP TABLE learning_git_changes")
        bridge.db.commit()
        bridge.sync_learning_git_changes()  # Should not raise

    def test_sync_manager_critiques_handles_missing_table(self, tmp_path):
        bridge, mock_session = _create_bridge(tmp_path)
        bridge.db.execute("DROP TABLE manager_critiques")
        bridge.db.commit()
        bridge.sync_manager_critiques()  # Should not raise

    def test_sync_risk_reviews_handles_missing_table(self, tmp_path):
        bridge, mock_session = _create_bridge(tmp_path)
        bridge.db.execute("DROP TABLE risk_officer_reviews")
        bridge.db.commit()
        bridge.sync_risk_reviews()  # Should not raise

    def test_sync_strategist_assessments_handles_missing_table(self, tmp_path):
        bridge, mock_session = _create_bridge(tmp_path)
        bridge.db.execute("DROP TABLE strategist_assessments")
        bridge.db.commit()
        bridge.sync_strategist_assessments()  # Should not raise

    def test_sync_conversations_handles_missing_table(self, tmp_path):
        bridge, mock_session = _create_bridge(tmp_path)
        bridge.db.execute("DROP TABLE conversations")
        bridge.db.commit()
        bridge.sync_conversations()  # Should not raise

    def test_sync_file_changes_handles_missing_table(self, tmp_path):
        bridge, mock_session = _create_bridge(tmp_path)
        bridge.db.execute("DROP TABLE file_change_audit")
        bridge.db.commit()
        bridge.sync_file_changes()  # Should not raise


# =========================================================================
# run loop
# =========================================================================

class TestRunLoop:

    def test_run_calls_tick_and_save_state(self, tmp_path):
        bridge, _ = _create_bridge(tmp_path)

        tick_count = 0

        def side_effect_tick():
            nonlocal tick_count
            tick_count += 1
            if tick_count >= 2:
                raise KeyboardInterrupt("Stop test loop")

        bridge.tick = MagicMock(side_effect=side_effect_tick)
        bridge._save_state = MagicMock()

        with patch("time.sleep", side_effect=[None, KeyboardInterrupt("stop")]):
            with pytest.raises(KeyboardInterrupt):
                bridge.run()

        assert bridge.tick.call_count >= 1
        assert bridge._save_state.call_count >= 1

    def test_run_handles_tick_exception(self, tmp_path):
        bridge, _ = _create_bridge(tmp_path)

        call_count = 0

        def tick_side_effect():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("Simulated failure")
            raise KeyboardInterrupt("Stop")

        bridge.tick = MagicMock(side_effect=tick_side_effect)
        bridge._save_state = MagicMock()

        with patch("time.sleep", return_value=None):
            with pytest.raises(KeyboardInterrupt):
                bridge.run()

        # tick was called at least twice (first raises RuntimeError, second raises KeyboardInterrupt)
        assert bridge.tick.call_count == 2
        # _save_state is only called when tick succeeds (before exception propagates)
        # In the run() loop, _save_state is called after tick, so if tick raises,
        # the except catches it and we sleep then loop again.


# =========================================================================
# _save_state
# =========================================================================

class TestSaveState:

    def test_save_state_writes_json(self, tmp_path):
        bridge, _ = _create_bridge(tmp_path)
        bridge.state["last_trade_id"] = 42

        bridge._save_state()

        state_path = tmp_path / ".bridge_state.json"
        assert state_path.exists()
        saved = json.loads(state_path.read_text())
        assert saved["last_trade_id"] == 42

    def test_save_state_creates_parent_dirs(self, tmp_path):
        bridge, _ = _create_bridge(tmp_path)
        bridge.state_path = tmp_path / "subdir" / "state.json"

        bridge._save_state()

        assert bridge.state_path.exists()
