import aiosqlite
import json
import logging
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DB_PATH = Path(os.getenv("WORKER_SQLITE_PATH", str(PROJECT_ROOT / "db" / "polybot.db")))

# Singleton connection
_db: aiosqlite.Connection | None = None


async def _get_db() -> aiosqlite.Connection:
    global _db
    if _db is None:
        _db = await aiosqlite.connect(DB_PATH)
        _db.row_factory = aiosqlite.Row
        await _db.execute("PRAGMA journal_mode=WAL")
        await _db.execute("PRAGMA busy_timeout=5000")
    return _db


async def close_db():
    global _db
    if _db:
        await _db.close()
        _db = None


async def init_db():
    db = await _get_db()
    await db.executescript("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market_id TEXT NOT NULL,
            market_question TEXT,
            token_id TEXT,
            category TEXT DEFAULT 'other',
            side TEXT NOT NULL,
            outcome TEXT NOT NULL,
            size_usdc REAL NOT NULL,
            price REAL NOT NULL,
            intended_shares REAL,
            filled_shares REAL DEFAULT 0,
            avg_fill_price REAL,
            edge REAL,
            edge_net REAL,
            confidence REAL,
            reasoning TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            order_id TEXT,
            executed_at TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market_id TEXT NOT NULL,
            token_id TEXT NOT NULL,
            market_question TEXT,
            outcome TEXT NOT NULL,
            size REAL NOT NULL,
            avg_price REAL NOT NULL,
            current_price REAL,
            pnl_unrealized REAL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'open',
            category TEXT DEFAULT 'other',
            opened_at TEXT NOT NULL DEFAULT (datetime('now')),
            closed_at TEXT,
            UNIQUE(market_id, token_id)
        );

        CREATE TABLE IF NOT EXISTS analysis_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cycle_number INTEGER,
            markets_analyzed INTEGER,
            trades_proposed INTEGER,
            trades_executed INTEGER,
            raw_response TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS daily_stats (
            date TEXT PRIMARY KEY,
            total_traded_usdc REAL DEFAULT 0,
            trades_count INTEGER DEFAULT 0,
            pnl_realized REAL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS performance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_id INTEGER REFERENCES trades(id),
            market_id TEXT NOT NULL,
            market_question TEXT,
            side TEXT NOT NULL DEFAULT 'BUY',
            outcome_bet TEXT NOT NULL,
            price_at_entry REAL NOT NULL,
            filled_shares REAL,
            avg_fill_price REAL,
            size_usdc REAL NOT NULL,
            fees_estimated REAL DEFAULT 0,
            market_resolved INTEGER DEFAULT 0,
            actual_outcome TEXT,
            pnl_realized REAL DEFAULT 0,
            pnl_net REAL,
            was_correct INTEGER,
            resolved_at TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS high_water_mark (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            peak_value REAL NOT NULL DEFAULT 100.0,
            current_value REAL NOT NULL DEFAULT 100.0,
            max_drawdown_pct REAL NOT NULL DEFAULT 0.0,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS analysis_cache (
            market_id TEXT PRIMARY KEY,
            price_snapshot TEXT NOT NULL,
            analysis_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS bot_status (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS bot_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            label_fr TEXT,
            description_fr TEXT,
            category TEXT,
            value_type TEXT,
            choices TEXT,
            min_value REAL,
            max_value REAL,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS bot_commands (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            command TEXT NOT NULL,
            payload TEXT,
            status TEXT DEFAULT 'pending',
            result TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            executed_at TEXT
        );

        CREATE TABLE IF NOT EXISTS order_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_id INTEGER REFERENCES trades(id),
            order_id TEXT,
            event_type TEXT NOT NULL,
            status TEXT,
            size_matched REAL,
            new_fill REAL,
            avg_fill_price REAL,
            note TEXT,
            payload_json TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_order_events_trade_id ON order_events(trade_id);
        CREATE INDEX IF NOT EXISTS idx_order_events_created_at ON order_events(created_at);

        INSERT OR IGNORE INTO high_water_mark (id, peak_value, current_value)
        VALUES (1, 100.0, 100.0);

        CREATE TABLE IF NOT EXISTS learning_journal (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cycle_number INTEGER NOT NULL,
            trades_proposed INTEGER DEFAULT 0,
            trades_executed INTEGER DEFAULT 0,
            trades_skipped INTEGER DEFAULT 0,
            skipped_markets TEXT,
            retrospective_json TEXT,
            price_snapshots TEXT,
            outcome_accuracy REAL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS learning_insights (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            insight_type TEXT NOT NULL,
            description TEXT NOT NULL,
            evidence TEXT,
            proposed_action TEXT,
            severity TEXT DEFAULT 'info',
            status TEXT DEFAULT 'active',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS learning_proposals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            proposal_type TEXT NOT NULL,
            target TEXT NOT NULL,
            current_value TEXT,
            proposed_value TEXT NOT NULL,
            rationale TEXT NOT NULL,
            risk_level TEXT DEFAULT 'moderate',
            status TEXT DEFAULT 'pending',
            applied_at TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS learning_shadow (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cycle_number INTEGER NOT NULL,
            market_id TEXT NOT NULL,
            current_decision TEXT,
            shadow_decision TEXT,
            current_params TEXT,
            shadow_params TEXT,
            outcome_price REAL,
            checked_at TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS learning_git_changes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            proposal_id INTEGER,
            branch_name TEXT NOT NULL,
            commit_hash TEXT,
            remote_name TEXT DEFAULT 'origin',
            push_status TEXT DEFAULT 'pending',
            justification TEXT,
            files_changed TEXT,
            result TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS manager_critiques (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cycle_number INTEGER NOT NULL,
            critique_json TEXT NOT NULL,
            summary TEXT NOT NULL DEFAULT '',
            trading_quality_score INTEGER,
            risk_management_score INTEGER,
            strategy_effectiveness_score INTEGER,
            improvement_areas TEXT,
            code_changes_suggested TEXT,
            status TEXT DEFAULT 'pending',
            developer_result TEXT,
            branch_name TEXT,
            commit_hash TEXT,
            deploy_status TEXT,
            user_feedback TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            reviewed_at TEXT,
            deployed_at TEXT
        );

        CREATE TABLE IF NOT EXISTS strategist_assessments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            assessment_json TEXT NOT NULL,
            summary TEXT NOT NULL DEFAULT '',
            market_regime TEXT DEFAULT 'normal',
            regime_confidence REAL DEFAULT 0.5,
            allocation_score INTEGER,
            diversification_score INTEGER,
            category_allocation TEXT,
            recommendations TEXT,
            strategic_insights TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS risk_officer_reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cycle_number INTEGER,
            review_json TEXT NOT NULL,
            portfolio_risk_summary TEXT DEFAULT '',
            trades_reviewed INTEGER DEFAULT 0,
            trades_flagged INTEGER DEFAULT 0,
            trades_rejected INTEGER DEFAULT 0,
            parameter_recommendations TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            role TEXT NOT NULL,
            agent_name TEXT,
            message TEXT NOT NULL,
            action_taken TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_conversations_source ON conversations(source);
        CREATE INDEX IF NOT EXISTS idx_conversations_created ON conversations(created_at);

        CREATE TABLE IF NOT EXISTS file_change_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_path TEXT NOT NULL,
            change_type TEXT NOT NULL,
            tier INTEGER NOT NULL,
            agent_name TEXT NOT NULL,
            reason TEXT,
            diff_summary TEXT,
            backup_path TEXT,
            status TEXT DEFAULT 'pending',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS mm_quotes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market_id TEXT NOT NULL,
            token_id TEXT NOT NULL,
            bid_order_id TEXT,
            ask_order_id TEXT,
            bid_price REAL NOT NULL,
            ask_price REAL NOT NULL,
            mid_price REAL,
            size REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_mm_quotes_status ON mm_quotes(status);
        CREATE INDEX IF NOT EXISTS idx_mm_quotes_market ON mm_quotes(market_id);

        CREATE TABLE IF NOT EXISTS mm_inventory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market_id TEXT NOT NULL,
            token_id TEXT NOT NULL,
            net_position REAL NOT NULL DEFAULT 0,
            avg_entry_price REAL NOT NULL DEFAULT 0,
            unrealized_pnl REAL NOT NULL DEFAULT 0,
            realized_pnl REAL NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(market_id, token_id)
        );

        CREATE TABLE IF NOT EXISTS mm_fills (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            quote_id INTEGER REFERENCES mm_quotes(id),
            order_id TEXT NOT NULL,
            side TEXT NOT NULL,
            price REAL NOT NULL,
            size REAL NOT NULL,
            fee REAL DEFAULT 0,
            mid_at_fill REAL,
            mid_at_30s REAL,
            mid_at_120s REAL,
            adverse_selection REAL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_mm_fills_quote ON mm_fills(quote_id);
        CREATE INDEX IF NOT EXISTS idx_mm_fills_created ON mm_fills(created_at);

        CREATE TABLE IF NOT EXISTS mm_round_trips (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market_id TEXT NOT NULL,
            token_id TEXT NOT NULL,
            entry_fill_id INTEGER REFERENCES mm_fills(id),
            exit_fill_id INTEGER REFERENCES mm_fills(id),
            entry_price REAL NOT NULL,
            exit_price REAL NOT NULL,
            size REAL NOT NULL,
            gross_pnl REAL NOT NULL,
            net_pnl REAL NOT NULL,
            hold_seconds REAL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS mm_daily_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL UNIQUE,
            markets_quoted INTEGER DEFAULT 0,
            quotes_placed INTEGER DEFAULT 0,
            fills_count INTEGER DEFAULT 0,
            round_trips INTEGER DEFAULT 0,
            spread_capture_rate REAL DEFAULT 0,
            fill_quality_avg REAL DEFAULT 0,
            adverse_selection_avg REAL DEFAULT 0,
            pnl_gross REAL DEFAULT 0,
            pnl_net REAL DEFAULT 0,
            max_inventory REAL DEFAULT 0,
            inventory_turns REAL DEFAULT 0,
            profit_factor REAL DEFAULT 0,
            sharpe_7d REAL DEFAULT 0,
            portfolio_value REAL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS cd_signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market_id TEXT NOT NULL,
            token_id TEXT,
            coin TEXT NOT NULL,
            strike REAL NOT NULL,
            expiry_days REAL NOT NULL,
            spot_price REAL NOT NULL,
            vol_ewma REAL NOT NULL,
            p_model REAL NOT NULL,
            p_market REAL NOT NULL,
            edge_pts REAL NOT NULL,
            confirmation_count INTEGER DEFAULT 1,
            action TEXT DEFAULT 'none',
            size_usdc REAL,
            order_id TEXT,
            ai_validation TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_cd_signals_market ON cd_signals(market_id);
        CREATE INDEX IF NOT EXISTS idx_cd_signals_created ON cd_signals(created_at);

        CREATE TABLE IF NOT EXISTS cd_positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market_id TEXT NOT NULL,
            token_id TEXT NOT NULL,
            coin TEXT NOT NULL,
            strike REAL NOT NULL,
            direction TEXT NOT NULL,
            entry_price REAL NOT NULL,
            shares REAL NOT NULL,
            expiry_days REAL,
            order_id TEXT,
            status TEXT NOT NULL DEFAULT 'open',
            exit_price REAL,
            exit_order_id TEXT,
            exit_reason TEXT,
            pnl_realized REAL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            closed_at TEXT,
            UNIQUE(market_id, token_id)
        );

        CREATE INDEX IF NOT EXISTS idx_cd_positions_status ON cd_positions(status);

        CREATE TABLE IF NOT EXISTS cd_trade_analyses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            analysis_type TEXT NOT NULL DEFAULT 'periodic',
            signals_analyzed INTEGER DEFAULT 0,
            positions_analyzed INTEGER DEFAULT 0,
            accuracy_score REAL,
            entry_quality_score REAL,
            exit_quality_score REAL,
            model_fitness_score REAL,
            overall_score REAL,
            parameter_suggestions TEXT,
            insights TEXT,
            summary TEXT,
            raw_response TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
    """)
    # Migrations: add strategy column if missing
    try:
        await db.execute("ALTER TABLE trades ADD COLUMN strategy TEXT DEFAULT 'active'")
    except Exception:
        pass  # Column already exists
    try:
        await db.execute("ALTER TABLE trades ADD COLUMN token_id TEXT")
    except Exception:
        pass  # Column already exists
    try:
        await db.execute("ALTER TABLE trades ADD COLUMN category TEXT DEFAULT 'other'")
    except Exception:
        pass
    try:
        await db.execute("ALTER TABLE trades ADD COLUMN edge_net REAL")
    except Exception:
        pass
    try:
        await db.execute("ALTER TABLE positions ADD COLUMN strategy TEXT DEFAULT 'active'")
    except Exception:
        pass  # Column already exists
    # Trades execution quality fields
    for sql in (
        "ALTER TABLE trades ADD COLUMN intended_shares REAL",
        "ALTER TABLE trades ADD COLUMN filled_shares REAL DEFAULT 0",
        "ALTER TABLE trades ADD COLUMN avg_fill_price REAL",
    ):
        try:
            await db.execute(sql)
        except Exception:
            pass
    # Performance side-aware + net fields
    for sql in (
        "ALTER TABLE performance ADD COLUMN side TEXT DEFAULT 'BUY'",
        "ALTER TABLE performance ADD COLUMN filled_shares REAL",
        "ALTER TABLE performance ADD COLUMN avg_fill_price REAL",
        "ALTER TABLE performance ADD COLUMN fees_estimated REAL DEFAULT 0",
        "ALTER TABLE performance ADD COLUMN pnl_net REAL",
    ):
        try:
            await db.execute(sql)
        except Exception:
            pass
    # Backfill historical rows created before pnl_net existed.
    # For resolved rows without fees where pnl_net stayed at default 0, copy pnl_realized.
    try:
        await db.execute(
            """UPDATE performance
               SET pnl_net = pnl_realized
               WHERE market_resolved=1
                 AND COALESCE(fees_estimated, 0)=0
                 AND COALESCE(pnl_realized, 0) != 0
                 AND COALESCE(pnl_net, 0)=0"""
        )
    except Exception:
        pass
    # CD positions: add expiry_days column
    try:
        await db.execute("ALTER TABLE cd_positions ADD COLUMN expiry_days REAL")
    except Exception:
        pass
    # CD signals: add ai_validation column
    try:
        await db.execute("ALTER TABLE cd_signals ADD COLUMN ai_validation TEXT")
    except Exception:
        pass
    await db.commit()


async def migrate_db():
    """Apply schema migrations for existing databases."""
    db = await _get_db()
    # Phase 5A: add portfolio_value, profit_factor, sharpe_7d to mm_daily_metrics
    try:
        await db.execute("ALTER TABLE mm_daily_metrics ADD COLUMN profit_factor REAL DEFAULT 0")
    except Exception:
        pass  # Column already exists
    try:
        await db.execute("ALTER TABLE mm_daily_metrics ADD COLUMN sharpe_7d REAL DEFAULT 0")
    except Exception:
        pass
    try:
        await db.execute("ALTER TABLE mm_daily_metrics ADD COLUMN portfolio_value REAL DEFAULT 0")
    except Exception:
        pass
    await db.commit()


# ═══════════════════════════════════════════════════════════════════════
# TRADES
# ═══════════════════════════════════════════════════════════════════════

async def insert_trade(trade: dict) -> int:
    db = await _get_db()
    cursor = await db.execute(
        """INSERT INTO trades (market_id, market_question, token_id, category, side, outcome,
           size_usdc, price, intended_shares, filled_shares, avg_fill_price,
           edge, edge_net, confidence, reasoning, status, strategy)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (trade["market_id"], trade.get("market_question"), trade.get("token_id"),
         trade.get("category", "other"), trade["side"], trade["outcome"],
         trade["size_usdc"], trade["price"],
         trade.get("intended_shares"), trade.get("filled_shares", 0),
         trade.get("avg_fill_price"),
         trade.get("edge"), trade.get("edge_net"), trade.get("confidence"),
         trade.get("reasoning"), trade.get("status", "pending"),
         trade.get("strategy", "active"))
    )
    await db.commit()
    return cursor.lastrowid


async def update_trade_status(trade_id: int, status: str, order_id: str = None):
    db = await _get_db()
    if order_id:
        await db.execute(
            "UPDATE trades SET status=?, order_id=?, executed_at=? WHERE id=?",
            (status, order_id, datetime.now(timezone.utc).isoformat(), trade_id)
        )
    else:
        await db.execute(
            "UPDATE trades SET status=? WHERE id=?", (status, trade_id)
        )
    await db.commit()


async def update_trade_execution_plan(
    trade_id: int,
    price: float,
    size_usdc: float,
    intended_shares: float,
):
    """Persist final execution plan before posting the order."""
    db = await _get_db()
    await db.execute(
        """UPDATE trades
           SET price=?, size_usdc=?, intended_shares=?
           WHERE id=?""",
        (price, size_usdc, intended_shares, trade_id),
    )
    await db.commit()


async def update_trade_fill_progress(
    trade_id: int,
    filled_shares: float,
    avg_fill_price: float | None = None,
):
    """Persist fill progress for partially/fully matched orders."""
    db = await _get_db()
    await db.execute(
        """UPDATE trades
           SET filled_shares = CASE
               WHEN COALESCE(filled_shares, 0) > ? THEN COALESCE(filled_shares, 0)
               ELSE ?
           END,
           avg_fill_price = COALESCE(?, avg_fill_price)
           WHERE id=?""",
        (filled_shares, filled_shares, avg_fill_price, trade_id),
    )
    await db.commit()


async def get_trades(limit: int = 50) -> list[dict]:
    db = await _get_db()
    cursor = await db.execute(
        "SELECT * FROM trades ORDER BY created_at DESC LIMIT ?", (limit,)
    )
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def get_pending_trades() -> list[dict]:
    db = await _get_db()
    cursor = await db.execute(
        "SELECT * FROM trades WHERE status='pending_confirmation'"
    )
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def get_pending_confirmation_trades() -> list[dict]:
    """Get all trades awaiting Telegram confirmation (for reload on restart)."""
    db = await _get_db()
    cursor = await db.execute(
        "SELECT * FROM trades WHERE status='pending_confirmation' ORDER BY created_at DESC"
    )
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def get_trades_with_order_status(status: str = "order_placed") -> list[dict]:
    """Get trades that have been placed but not yet confirmed filled."""
    db = await _get_db()
    cursor = await db.execute(
        "SELECT * FROM trades WHERE status=? ORDER BY created_at ASC", (status,)
    )
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def get_trades_by_status(status: str) -> list[dict]:
    db = await _get_db()
    cursor = await db.execute("SELECT * FROM trades WHERE status = ?", (status,))
    rows = await cursor.fetchall()
    return [dict(row) for row in rows]


async def insert_order_event(
    trade_id: int,
    event_type: str,
    *,
    order_id: str | None = None,
    status: str | None = None,
    size_matched: float | None = None,
    new_fill: float | None = None,
    avg_fill_price: float | None = None,
    note: str | None = None,
    payload: dict | None = None,
) -> int:
    """Append an execution lifecycle event for auditability."""
    db = await _get_db()
    payload_json = json.dumps(payload, default=str) if payload is not None else None
    cursor = await db.execute(
        """INSERT INTO order_events
           (trade_id, order_id, event_type, status, size_matched, new_fill,
            avg_fill_price, note, payload_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            trade_id,
            order_id,
            event_type,
            status,
            size_matched,
            new_fill,
            avg_fill_price,
            note,
            payload_json,
        ),
    )
    await db.commit()
    return cursor.lastrowid


async def get_recent_order_events(limit: int = 100) -> list[dict]:
    db = await _get_db()
    cursor = await db.execute(
        "SELECT * FROM order_events ORDER BY created_at DESC LIMIT ?",
        (limit,),
    )
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def get_execution_quality_stats(days: int = 7) -> dict:
    """Execution-quality KPIs from trades + order lifecycle events."""
    db = await _get_db()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    cursor = await db.execute(
        """SELECT COUNT(*) AS total
           FROM trades
           WHERE order_id IS NOT NULL
             AND order_id != 'PAPER'
             AND created_at >= ?""",
        (cutoff,),
    )
    total_orders = int((await cursor.fetchone())["total"] or 0)

    cursor = await db.execute(
        """SELECT COUNT(*) AS executed
           FROM trades
           WHERE status='executed' AND created_at >= ?""",
        (cutoff,),
    )
    executed_orders = int((await cursor.fetchone())["executed"] or 0)

    cursor = await db.execute(
        """SELECT COUNT(*) AS cancelled
           FROM trades
           WHERE status IN ('cancelled', 'timeout_cancelled')
             AND created_at >= ?""",
        (cutoff,),
    )
    cancelled_orders = int((await cursor.fetchone())["cancelled"] or 0)

    cursor = await db.execute(
        """SELECT COUNT(*) AS partials
           FROM (
               SELECT DISTINCT trade_id
               FROM order_events
               WHERE event_type='partial_fill'
                 AND created_at >= ?
           )""",
        (cutoff,),
    )
    partial_orders = int((await cursor.fetchone())["partials"] or 0)

    cursor = await db.execute(
        """SELECT COALESCE(AVG(
                   CASE
                       WHEN intended_shares > 0
                       THEN MIN(COALESCE(filled_shares, 0) / intended_shares, 1.0)
                       ELSE NULL
                   END
               ), 0) AS avg_fill_ratio
           FROM trades
           WHERE created_at >= ?""",
        (cutoff,),
    )
    avg_fill_ratio = float((await cursor.fetchone())["avg_fill_ratio"] or 0.0)

    cursor = await db.execute(
        """SELECT COALESCE(AVG(
                   ABS((COALESCE(avg_fill_price, price) - price) / price) * 10000
               ), 0) AS avg_slippage_bps
           FROM trades
           WHERE COALESCE(filled_shares, 0) > 0
             AND price > 0
             AND avg_fill_price IS NOT NULL
             AND created_at >= ?""",
        (cutoff,),
    )
    avg_slippage_bps = float((await cursor.fetchone())["avg_slippage_bps"] or 0.0)

    cursor = await db.execute(
        """SELECT COALESCE(AVG(
                   (julianday(executed_at) - julianday(created_at)) * 86400.0
               ), 0) AS avg_latency_sec
           FROM trades
           WHERE status='executed'
             AND executed_at IS NOT NULL
             AND created_at >= ?""",
        (cutoff,),
    )
    avg_latency_sec = float((await cursor.fetchone())["avg_latency_sec"] or 0.0)

    fill_rate = executed_orders / total_orders if total_orders else 0.0
    cancel_rate = cancelled_orders / total_orders if total_orders else 0.0
    partial_rate = partial_orders / total_orders if total_orders else 0.0

    return {
        "window_days": days,
        "total_orders": total_orders,
        "executed_orders": executed_orders,
        "cancelled_orders": cancelled_orders,
        "partial_orders": partial_orders,
        "fill_rate": round(fill_rate, 4),
        "cancel_rate": round(cancel_rate, 4),
        "partial_rate": round(partial_rate, 4),
        "avg_fill_ratio": round(avg_fill_ratio, 4),
        "avg_slippage_bps": round(avg_slippage_bps, 1),
        "avg_latency_sec": round(avg_latency_sec, 1),
    }


# ═══════════════════════════════════════════════════════════════════════
# POSITIONS
# ═══════════════════════════════════════════════════════════════════════

async def upsert_position(position: dict):
    db = await _get_db()
    await db.execute(
        """INSERT INTO positions (market_id, token_id, market_question,
           outcome, size, avg_price, current_price, category, strategy, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'open')
           ON CONFLICT(market_id, token_id) DO UPDATE SET
           size = CASE WHEN positions.status = 'closed'
                       THEN excluded.size
                       ELSE positions.size + excluded.size END,
           avg_price = CASE WHEN positions.status = 'closed'
                       THEN excluded.avg_price
                       ELSE (positions.avg_price * positions.size + excluded.avg_price * excluded.size)
                            / (positions.size + excluded.size) END,
           current_price = excluded.current_price,
           status = 'open',
           opened_at = CASE WHEN positions.status = 'closed'
                       THEN datetime('now') ELSE positions.opened_at END""",
        (position["market_id"], position["token_id"],
         position.get("market_question"), position["outcome"],
         position["size"], position["avg_price"],
         position.get("current_price"), position.get("category", "other"),
         position.get("strategy", "active"))
    )
    await db.commit()


async def get_open_positions() -> list[dict]:
    db = await _get_db()
    cursor = await db.execute(
        "SELECT * FROM positions WHERE status='open'"
    )
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def close_position(market_id: str, token_id: str):
    db = await _get_db()
    await db.execute(
        "UPDATE positions SET status='closed', closed_at=? WHERE market_id=? AND token_id=?",
        (datetime.now(timezone.utc).isoformat(), market_id, token_id)
    )
    await db.commit()


async def reduce_position(
    market_id: str,
    token_id: str,
    shares_sold: float,
    sell_price: float | None = None,
) -> dict | None:
    """Reduce position size after a sell and return execution metadata."""
    db = await _get_db()
    cursor = await db.execute(
        "SELECT size, avg_price FROM positions WHERE market_id=? AND token_id=? AND status='open'",
        (market_id, token_id)
    )
    row = await cursor.fetchone()
    if not row:
        return None

    current_size = float(row["size"] or 0)
    avg_price = float(row["avg_price"] or 0)
    sold = max(0.0, min(float(shares_sold or 0), current_size))
    remaining = current_size - sold

    if remaining <= 0.01:
        await close_position(market_id, token_id)
    else:
        await db.execute(
            "UPDATE positions SET size=? WHERE market_id=? AND token_id=? AND status='open'",
            (remaining, market_id, token_id)
        )
        await db.commit()

    realized_pnl = None
    if sell_price is not None:
        realized_pnl = (float(sell_price) - avg_price) * sold

    return {
        "entry_avg_price": avg_price,
        "shares_sold": sold,
        "remaining_shares": max(0.0, remaining),
        "realized_pnl": realized_pnl,
    }


async def get_positions_by_strategy(strategy: str) -> list[dict]:
    """Get open positions tagged with a specific strategy."""
    db = await _get_db()
    cursor = await db.execute(
        "SELECT * FROM positions WHERE status='open' AND strategy=?", (strategy,)
    )
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def get_positions_by_category(category: str) -> list[dict]:
    db = await _get_db()
    cursor = await db.execute(
        "SELECT * FROM positions WHERE status='open' AND category=?", (category,)
    )
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def get_category_exposure() -> dict[str, float]:
    """Get total USDC exposure per category for concentration checks."""
    db = await _get_db()
    cursor = await db.execute(
        """SELECT category, SUM(size * avg_price) as exposure
           FROM positions WHERE status='open'
           GROUP BY category"""
    )
    rows = await cursor.fetchall()
    return {row["category"]: row["exposure"] for row in rows}


# ═══════════════════════════════════════════════════════════════════════
# DAILY STATS
# ═══════════════════════════════════════════════════════════════════════

async def get_daily_traded(date_str: str = None) -> float:
    if not date_str:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    db = await _get_db()
    cursor = await db.execute(
        "SELECT COALESCE(total_traded_usdc, 0) FROM daily_stats WHERE date=?",
        (date_str,)
    )
    row = await cursor.fetchone()
    return row[0] if row else 0.0


async def increment_daily_traded(amount: float, date_str: str = None):
    if not date_str:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    db = await _get_db()
    await db.execute(
        """INSERT INTO daily_stats (date, total_traded_usdc, trades_count)
           VALUES (?, ?, 1)
           ON CONFLICT(date) DO UPDATE SET
           total_traded_usdc = total_traded_usdc + excluded.total_traded_usdc,
           trades_count = trades_count + 1""",
        (date_str, amount)
    )
    await db.commit()


async def decrement_daily_traded(amount: float, date_str: str = None):
    if date_str is None:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    db = await _get_db()
    await db.execute(
        "UPDATE daily_stats SET total_traded_usdc = MAX(0, total_traded_usdc - ?) WHERE date = ?",
        (amount, date_str)
    )
    await db.commit()


async def update_daily_pnl(pnl_realized: float, date_str: str = None):
    if not date_str:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    db = await _get_db()
    await db.execute(
        """INSERT INTO daily_stats (date, pnl_realized)
           VALUES (?, ?)
           ON CONFLICT(date) DO UPDATE SET
           pnl_realized = pnl_realized + excluded.pnl_realized""",
        (date_str, pnl_realized)
    )
    await db.commit()


# ═══════════════════════════════════════════════════════════════════════
# ANALYSIS LOG
# ═══════════════════════════════════════════════════════════════════════

async def log_analysis(cycle: int, markets: int, proposed: int,
                       executed: int, raw: str):
    db = await _get_db()
    await db.execute(
        """INSERT INTO analysis_log
           (cycle_number, markets_analyzed, trades_proposed,
            trades_executed, raw_response)
           VALUES (?, ?, ?, ?, ?)""",
        (cycle, markets, proposed, executed, raw)
    )
    await db.commit()


# ═══════════════════════════════════════════════════════════════════════
# PERFORMANCE TRACKING
# ═══════════════════════════════════════════════════════════════════════

async def insert_performance(trade_id: int, market_id: str, market_question: str,
                             outcome_bet: str, price_at_entry: float,
                             size_usdc: float, side: str = "BUY",
                             filled_shares: float | None = None,
                             avg_fill_price: float | None = None,
                             fees_estimated: float = 0.0,
                             market_resolved: int = 0,
                             actual_outcome: str | None = None,
                             pnl_realized: float = 0.0,
                             pnl_net: float = 0.0,
                             was_correct: int | None = None,
                             resolved_at: str | None = None) -> int:
    db = await _get_db()
    cursor = await db.execute(
        """INSERT INTO performance (trade_id, market_id, market_question,
           side, outcome_bet, price_at_entry, filled_shares, avg_fill_price,
           size_usdc, fees_estimated, market_resolved, actual_outcome,
           pnl_realized, pnl_net, was_correct, resolved_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            trade_id, market_id, market_question, side, outcome_bet, price_at_entry,
            filled_shares, avg_fill_price, size_usdc, fees_estimated,
            market_resolved, actual_outcome, pnl_realized, pnl_net, was_correct,
            resolved_at,
        )
    )
    await db.commit()
    return cursor.lastrowid


async def resolve_performance(market_id: str, actual_outcome: str):
    """Resolve all unresolved performance records for a market."""
    db = await _get_db()
    now = datetime.now(timezone.utc).isoformat()
    pnl_net_total = 0.0

    # Get unresolved records for this market
    cursor = await db.execute(
        "SELECT * FROM performance WHERE market_id=? AND market_resolved=0",
        (market_id,)
    )
    rows = await cursor.fetchall()

    for row in rows:
        row = dict(row)
        side = (row.get("side") or "BUY").upper()
        if side != "BUY":
            # SELL trades should normally be resolved at execution time.
            # If one slips through unresolved, mark it resolved without recomputing.
            await db.execute(
                """UPDATE performance SET market_resolved=1, actual_outcome=?,
                   resolved_at=?
                   WHERE id=?""",
                (actual_outcome, now, row["id"]),
            )
            continue

        was_correct = 1 if row["outcome_bet"].lower() == actual_outcome.lower() else 0
        if was_correct:
            # Won: each filled share pays $1 at resolution.
            filled = row.get("filled_shares")
            if filled is not None:
                pnl = float(filled) - float(row["size_usdc"])
            else:
                price_at_entry = float(row["price_at_entry"] or 0)
                pnl = (float(row["size_usdc"]) / price_at_entry - float(row["size_usdc"])) if price_at_entry > 0 else 0.0
        else:
            # Lost: lose the entire stake
            pnl = -row["size_usdc"]
        fees = float(row.get("fees_estimated") or 0)
        pnl_net = pnl - fees
        pnl_net_total += pnl_net

        await db.execute(
            """UPDATE performance SET market_resolved=1, actual_outcome=?,
               was_correct=?, pnl_realized=?, pnl_net=?, resolved_at=?
               WHERE id=?""",
            (actual_outcome, was_correct, pnl, pnl_net, now, row["id"])
        )

    await db.commit()
    return {"count": len(rows), "pnl_net_total": pnl_net_total}


async def get_unresolved_market_ids() -> list[str]:
    """Get distinct market IDs that have unresolved performance records."""
    db = await _get_db()
    cursor = await db.execute(
        "SELECT DISTINCT market_id FROM performance WHERE market_resolved=0"
    )
    rows = await cursor.fetchall()
    return [row["market_id"] for row in rows]


async def get_performance_stats() -> dict:
    """Comprehensive performance statistics."""
    db = await _get_db()

    # Overall stats
    cursor = await db.execute("SELECT COUNT(*) as total FROM performance")
    total = (await cursor.fetchone())["total"]

    cursor = await db.execute(
        "SELECT COUNT(*) as resolved FROM performance WHERE market_resolved=1"
    )
    resolved = (await cursor.fetchone())["resolved"]

    if resolved == 0:
        return {
            "total_trades": total,
            "resolved_trades": 0,
            "pending_resolution": total,
            "wins": 0, "losses": 0, "hit_rate": 0.0,
            "total_pnl": 0.0, "avg_pnl_per_trade": 0.0,
            "best_trade": 0.0, "worst_trade": 0.0,
            "total_wagered": 0.0, "roi_percent": 0.0,
            "current_streak": 0, "streak_type": "none",
        }

    cursor = await db.execute(
        "SELECT COUNT(*) as wins FROM performance WHERE market_resolved=1 AND was_correct=1"
    )
    wins = (await cursor.fetchone())["wins"]
    cursor = await db.execute(
        "SELECT COUNT(*) as losses FROM performance WHERE market_resolved=1 AND was_correct=0"
    )
    losses = (await cursor.fetchone())["losses"]

    cursor = await db.execute(
        """SELECT COALESCE(
               SUM(
                   CASE
                       WHEN pnl_net IS NOT NULL THEN pnl_net
                       ELSE pnl_realized
                   END
               ),
               0
           ) as total_pnl
           FROM performance
           WHERE market_resolved=1"""
    )
    total_pnl = (await cursor.fetchone())["total_pnl"]

    cursor = await db.execute(
        "SELECT COALESCE(SUM(size_usdc), 0) as total_wagered FROM performance WHERE market_resolved=1"
    )
    total_wagered = (await cursor.fetchone())["total_wagered"]

    cursor = await db.execute(
        """SELECT COALESCE(
               MAX(
                   CASE
                       WHEN pnl_net IS NOT NULL THEN pnl_net
                       ELSE pnl_realized
                   END
               ),
               0
           ) as best
           FROM performance
           WHERE market_resolved=1"""
    )
    best = (await cursor.fetchone())["best"]

    cursor = await db.execute(
        """SELECT COALESCE(
               MIN(
                   CASE
                       WHEN pnl_net IS NOT NULL THEN pnl_net
                       ELSE pnl_realized
                   END
               ),
               0
           ) as worst
           FROM performance
           WHERE market_resolved=1"""
    )
    worst = (await cursor.fetchone())["worst"]

    # Current streak
    cursor = await db.execute(
        """SELECT was_correct FROM performance
           WHERE market_resolved=1 AND was_correct IN (0, 1)
           ORDER BY resolved_at DESC LIMIT 20"""
    )
    recent = [dict(r)["was_correct"] for r in await cursor.fetchall()]
    streak = 0
    streak_type = "none"
    if recent:
        streak_type = "win" if recent[0] == 1 else "loss"
        for r in recent:
            if (r == 1 and streak_type == "win") or (r == 0 and streak_type == "loss"):
                streak += 1
            else:
                break

    graded = wins + losses
    return {
        "total_trades": total,
        "resolved_trades": resolved,
        "pending_resolution": total - resolved,
        "wins": wins,
        "losses": losses,
        "hit_rate": wins / graded if graded else 0.0,
        "total_pnl": round(total_pnl, 2),
        "avg_pnl_per_trade": round(total_pnl / resolved, 2) if resolved else 0.0,
        "best_trade": round(best, 2),
        "worst_trade": round(worst, 2),
        "total_wagered": round(total_wagered, 2),
        "roi_percent": round(total_pnl / total_wagered * 100, 2) if total_wagered else 0.0,
        "current_streak": streak,
        "streak_type": streak_type,
    }


async def get_performance_attribution() -> dict:
    """PnL attribution split by strategy and category for resolved trades."""
    db = await _get_db()

    def _normalize(rows: list[aiosqlite.Row], key_name: str) -> list[dict]:
        items = []
        for row in rows:
            pnl = float(row["pnl"] or 0.0)
            wagered = float(row["wagered"] or 0.0)
            items.append({
                key_name: row["group_key"],
                "trades": int(row["trades"] or 0),
                "pnl": round(pnl, 2),
                "wagered": round(wagered, 2),
                "roi_percent": round((pnl / wagered * 100.0), 2) if wagered > 0 else 0.0,
            })
        return items

    pnl_expr = "CASE WHEN p.pnl_net IS NOT NULL THEN p.pnl_net ELSE p.pnl_realized END"

    cursor = await db.execute(
        f"""SELECT COALESCE(t.strategy, 'unknown') AS group_key,
                   COUNT(*) AS trades,
                   COALESCE(SUM({pnl_expr}), 0) AS pnl,
                   COALESCE(SUM(p.size_usdc), 0) AS wagered
            FROM performance p
            LEFT JOIN trades t ON p.trade_id=t.id
            WHERE p.market_resolved=1
            GROUP BY COALESCE(t.strategy, 'unknown')
            ORDER BY pnl DESC"""
    )
    strategy_rows = await cursor.fetchall()

    cursor = await db.execute(
        f"""SELECT COALESCE(t.category, 'other') AS group_key,
                   COUNT(*) AS trades,
                   COALESCE(SUM({pnl_expr}), 0) AS pnl,
                   COALESCE(SUM(p.size_usdc), 0) AS wagered
            FROM performance p
            LEFT JOIN trades t ON p.trade_id=t.id
            WHERE p.market_resolved=1
            GROUP BY COALESCE(t.category, 'other')
            ORDER BY pnl DESC"""
    )
    category_rows = await cursor.fetchall()

    return {
        "by_strategy": _normalize(strategy_rows, "strategy"),
        "by_category": _normalize(category_rows, "category"),
    }


async def get_calibration_data() -> list[dict]:
    """Get resolved trades for calibration analysis (bias detection)."""
    db = await _get_db()
    cursor = await db.execute(
        """SELECT p.*, t.edge, t.confidence
           FROM performance p
           LEFT JOIN trades t ON p.trade_id = t.id
           WHERE p.market_resolved=1
           ORDER BY p.resolved_at DESC LIMIT 200"""
    )
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


# ═══════════════════════════════════════════════════════════════════════
# HIGH WATER MARK
# ═══════════════════════════════════════════════════════════════════════

async def get_high_water_mark() -> dict:
    db = await _get_db()
    cursor = await db.execute("SELECT * FROM high_water_mark WHERE id=1")
    row = await cursor.fetchone()
    return dict(row) if row else {"peak_value": 100.0, "current_value": 100.0, "max_drawdown_pct": 0.0}


async def update_high_water_mark(current_value: float):
    db = await _get_db()
    hwm = await get_high_water_mark()
    new_peak = max(hwm["peak_value"], current_value)
    drawdown_pct = (new_peak - current_value) / new_peak * 100 if new_peak > 0 else 0
    max_dd = max(hwm["max_drawdown_pct"], drawdown_pct)
    now = datetime.now(timezone.utc).isoformat()

    await db.execute(
        """UPDATE high_water_mark SET
           peak_value=?, current_value=?, max_drawdown_pct=?, updated_at=?
           WHERE id=1""",
        (new_peak, current_value, max_dd, now)
    )
    await db.commit()


# ═══════════════════════════════════════════════════════════════════════
# ANALYSIS CACHE
# ═══════════════════════════════════════════════════════════════════════

async def get_cached_analysis(market_id: str, max_age_minutes: int = 25) -> dict | None:
    db = await _get_db()
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=max_age_minutes)).isoformat()
    cursor = await db.execute(
        "SELECT * FROM analysis_cache WHERE market_id=? AND created_at > ?",
        (market_id, cutoff)
    )
    row = await cursor.fetchone()
    if row:
        try:
            return json.loads(dict(row)["analysis_json"])
        except (json.JSONDecodeError, KeyError):
            return None
    return None


async def set_cached_analysis(market_id: str, price_snapshot: str, analysis_json: str):
    db = await _get_db()
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        """INSERT INTO analysis_cache (market_id, price_snapshot, analysis_json, created_at)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(market_id) DO UPDATE SET
           price_snapshot=excluded.price_snapshot,
           analysis_json=excluded.analysis_json,
           created_at=excluded.created_at""",
        (market_id, price_snapshot, analysis_json, now)
    )
    await db.commit()


async def cleanup_old_cache(max_age_hours: int = 24):
    db = await _get_db()
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=max_age_hours)).isoformat()
    await db.execute("DELETE FROM analysis_cache WHERE created_at < ?", (cutoff,))
    await db.commit()


# ═══════════════════════════════════════════════════════════════════════
# BOT STATUS (shared state between bot process and dashboard process)
# ═══════════════════════════════════════════════════════════════════════

async def update_bot_status(status: dict):
    """Write multiple key-value pairs to bot_status table."""
    db = await _get_db()
    now = datetime.now(timezone.utc).isoformat()
    for key, value in status.items():
        await db.execute(
            """INSERT INTO bot_status (key, value, updated_at)
               VALUES (?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
            (key, json.dumps(value) if not isinstance(value, str) else value, now)
        )
    await db.commit()


async def get_bot_status() -> dict:
    """Read all bot_status entries as a dict."""
    db = await _get_db()
    rows = await db.execute_fetchall("SELECT key, value FROM bot_status")
    result = {}
    for row in rows:
        k, v = row["key"], row["value"]
        try:
            result[k] = json.loads(v)
        except (json.JSONDecodeError, TypeError):
            result[k] = v
    return result


async def get_bot_status_field(key: str) -> str | None:
    """Read a single bot_status value by key."""
    db = await _get_db()
    cursor = await db.execute("SELECT value FROM bot_status WHERE key=?", (key,))
    row = await cursor.fetchone()
    return row["value"] if row else None


# ═══════════════════════════════════════════════════════════════════════
# BOT SETTINGS (runtime-editable config via dashboard)
# ═══════════════════════════════════════════════════════════════════════

SETTINGS_DEFINITIONS = {
    "strategy": {
        "label_fr": "Strategie (fixe)",
        "description_fr": "Mode unique de ce bot: ACTIVE (achat + vente). Les strategies multiples sont desactivees pour concentrer l'apprentissage.",
        "category": "trading",
        "value_type": "choice",
        "choices": '["active"]',
        "min_value": None,
        "max_value": None,
    },
    "max_per_day_usdc": {
        "label_fr": "Budget journalier ($)",
        "description_fr": "Montant maximum que le bot peut investir en une journee. Une fois ce budget atteint, le bot attend le lendemain.",
        "category": "trading",
        "value_type": "float",
        "choices": None,
        "min_value": 5,
        "max_value": 500,
    },
    "max_per_trade_usdc": {
        "label_fr": "Mise max par operation ($)",
        "description_fr": "Montant maximum que le bot peut investir sur un seul marche. Limite les risques en cas d'erreur de l'IA.",
        "category": "trading",
        "value_type": "float",
        "choices": None,
        "min_value": 1,
        "max_value": 100,
    },
    "confirmation_threshold_usdc": {
        "label_fr": "Seuil de confirmation ($)",
        "description_fr": "Au-dessus de ce montant, le bot te demande ta validation avant de trader. En dessous, il execute seul.",
        "category": "trading",
        "value_type": "float",
        "choices": None,
        "min_value": 1,
        "max_value": 50,
    },
    "stop_loss_percent": {
        "label_fr": "Stop-loss journalier (%)",
        "description_fr": "Si les pertes du jour depassent ce pourcentage du budget, le bot se met en pause automatiquement pour proteger ton capital.",
        "category": "risk",
        "value_type": "float",
        "choices": None,
        "min_value": 5,
        "max_value": 50,
    },
    "drawdown_stop_loss_percent": {
        "label_fr": "Stop-loss cumule (%)",
        "description_fr": "Si la valeur totale du portefeuille baisse de ce pourcentage par rapport a son plus haut, le bot se met en pause.",
        "category": "risk",
        "value_type": "float",
        "choices": None,
        "min_value": 10,
        "max_value": 50,
    },
    "min_edge_percent": {
        "label_fr": "Avantage minimum (%)",
        "description_fr": "L'IA doit estimer un avantage d'au moins ce pourcentage pour proposer un trade. Plus c'est eleve, moins de trades mais plus selectifs.",
        "category": "risk",
        "value_type": "float",
        "choices": None,
        "min_value": 3,
        "max_value": 30,
    },
    "min_net_edge_percent": {
        "label_fr": "Avantage net minimum (%)",
        "description_fr": "Avantage minimum apres estimation des couts d'execution (slippage + frais). Garde-fou prioritaire pour eviter les faux edges.",
        "category": "risk",
        "value_type": "float",
        "choices": None,
        "min_value": 1,
        "max_value": 30,
    },
    "max_slippage_bps": {
        "label_fr": "Slippage max (bps)",
        "description_fr": "Slippage maximal accepte (en points de base) pour valider un trade. Au-dela, le trade est rejete.",
        "category": "risk",
        "value_type": "float",
        "choices": None,
        "min_value": 10,
        "max_value": 2000,
    },
    "min_source_quality": {
        "label_fr": "Qualite source min",
        "description_fr": "Score minimum (0-1) de qualite de l'information multi-sources requis pour executer un trade.",
        "category": "risk",
        "value_type": "float",
        "choices": None,
        "min_value": 0,
        "max_value": 1,
    },
    "estimated_fee_bps": {
        "label_fr": "Frais estimes (bps)",
        "description_fr": "Estimation conservative des frais d'execution en points de base, utilisee dans le calcul d'edge net.",
        "category": "risk",
        "value_type": "float",
        "choices": None,
        "min_value": 0,
        "max_value": 500,
    },
    "max_concentration_percent": {
        "label_fr": "Concentration max (%)",
        "description_fr": "Pourcentage maximum du budget investi dans une meme categorie (ex: politique, sport). Evite de tout miser sur le meme theme.",
        "category": "risk",
        "value_type": "float",
        "choices": None,
        "min_value": 10,
        "max_value": 100,
    },
    "analysis_interval_minutes": {
        "label_fr": "Intervalle d'analyse (min)",
        "description_fr": "Temps entre chaque cycle d'analyse. Le bot cherche de nouvelles opportunites a cette frequence.",
        "category": "cycle",
        "value_type": "int",
        "choices": None,
        "min_value": 5,
        "max_value": 120,
    },
    "learning_mode": {
        "label_fr": "Mode apprentissage",
        "description_fr": "Active le journal de retrospective, les insights et les propositions d'amelioration continue.",
        "category": "learning",
        "value_type": "bool",
        "choices": None,
        "min_value": None,
        "max_value": None,
    },
    "learning_review_interval": {
        "label_fr": "Frequence meta-analyse (cycles)",
        "description_fr": "Nombre de cycles entre deux revues globales du systeme d'apprentissage.",
        "category": "learning",
        "value_type": "int",
        "choices": None,
        "min_value": 1,
        "max_value": 100,
    },
    "learning_auto_apply": {
        "label_fr": "Auto-application config safe",
        "description_fr": "Applique automatiquement les changements de configuration marques 'safe'.",
        "category": "learning",
        "value_type": "bool",
        "choices": None,
        "min_value": None,
        "max_value": None,
    },
    "learning_auto_fix_logs": {
        "label_fr": "Auto-correction depuis logs",
        "description_fr": "Analyse les erreurs de logs et genere des corrections (prompt/code) avec suivi git.",
        "category": "learning",
        "value_type": "bool",
        "choices": None,
        "min_value": None,
        "max_value": None,
    },
    "learning_max_commits_per_day": {
        "label_fr": "Commits max par jour",
        "description_fr": "Nombre maximum de commits auto-correction pushes par jour.",
        "category": "learning",
        "value_type": "int",
        "choices": None,
        "min_value": 1,
        "max_value": 6,
    },
    "learning_git_enabled": {
        "label_fr": "Git learning active",
        "description_fr": "Autorise les commits de correction sur des branches dediees.",
        "category": "learning",
        "value_type": "bool",
        "choices": None,
        "min_value": None,
        "max_value": None,
    },
    "learning_git_push": {
        "label_fr": "Push auto des corrections",
        "description_fr": "Si active, pousse les branches de correction vers le remote git configure.",
        "category": "learning",
        "value_type": "bool",
        "choices": None,
        "min_value": None,
        "max_value": None,
    },
    "risk_officer_enabled": {
        "label_fr": "Risk Officer actif",
        "description_fr": "Active l'agent Risk Officer qui revoit chaque trade avant execution et peut bloquer les operations trop risquees.",
        "category": "agents",
        "value_type": "bool",
        "choices": None,
        "min_value": None,
        "max_value": None,
    },
    "strategist_enabled": {
        "label_fr": "Strategist actif",
        "description_fr": "Active l'agent Strategist qui analyse periodiquement l'allocation du portefeuille et le regime de marche.",
        "category": "agents",
        "value_type": "bool",
        "choices": None,
        "min_value": None,
        "max_value": None,
    },
    "strategist_review_interval": {
        "label_fr": "Frequence Strategist (cycles)",
        "description_fr": "Nombre de cycles entre deux analyses strategiques du portefeuille.",
        "category": "agents",
        "value_type": "int",
        "choices": None,
        "min_value": 1,
        "max_value": 50,
    },
    "conversation_enabled": {
        "label_fr": "Chat NL actif",
        "description_fr": "Active l'interface de conversation en langage naturel via Telegram et le dashboard.",
        "category": "agents",
        "value_type": "bool",
        "choices": None,
        "min_value": None,
        "max_value": None,
    },
    "conversation_max_history": {
        "label_fr": "Historique conversation",
        "description_fr": "Nombre de messages gardes en memoire pour le contexte des conversations.",
        "category": "agents",
        "value_type": "int",
        "choices": None,
        "min_value": 5,
        "max_value": 100,
    },
}


def _apply_setting_to_config(trading_config, key: str, value_str: str):
    """Apply a single DB setting value to the TradingConfig object."""
    meta = SETTINGS_DEFINITIONS.get(key)
    if not meta:
        return
    vtype = meta["value_type"]
    try:
        if vtype == "bool":
            typed_value = value_str.lower() in ("true", "1", "yes")
        elif vtype == "float":
            typed_value = float(value_str)
        elif vtype == "int":
            typed_value = int(float(value_str))
        elif vtype == "choice":
            typed_value = value_str
        else:
            typed_value = value_str
        setattr(trading_config, key, typed_value)
    except (ValueError, TypeError) as e:
        logger.warning(f"Cannot apply setting {key}={value_str}: {e}")


async def init_settings(trading_config):
    """Initialize bot_settings from TradingConfig. DB values take priority over .env."""
    db = await _get_db()
    now = datetime.now(timezone.utc).isoformat()
    keys = tuple(SETTINGS_DEFINITIONS.keys())
    placeholders = ",".join(["?"] * len(keys))

    # Remove deprecated runtime settings that are no longer supported.
    await db.execute(
        f"DELETE FROM bot_settings WHERE key NOT IN ({placeholders})",
        keys,
    )

    for key, meta in SETTINGS_DEFINITIONS.items():
        cursor = await db.execute("SELECT value FROM bot_settings WHERE key=?", (key,))
        row = await cursor.fetchone()

        if row is None:
            # First run: write current config value to DB
            current_value = getattr(trading_config, key, None)
            if current_value is None:
                continue
            value_str = str(current_value).lower() if isinstance(current_value, bool) else str(current_value)
            await db.execute(
                """INSERT INTO bot_settings (key, value, label_fr, description_fr,
                   category, value_type, choices, min_value, max_value, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (key, value_str, meta["label_fr"], meta["description_fr"],
                 meta["category"], meta["value_type"], meta["choices"],
                 meta["min_value"], meta["max_value"], now)
            )
        else:
            # Keep labels/metadata in sync when code definitions evolve.
            await db.execute(
                """UPDATE bot_settings
                   SET label_fr=?, description_fr=?, category=?, value_type=?,
                       choices=?, min_value=?, max_value=?
                   WHERE key=?""",
                (
                    meta["label_fr"], meta["description_fr"], meta["category"],
                    meta["value_type"], meta["choices"], meta["min_value"], meta["max_value"], key,
                ),
            )
            # Enforce single strategy mode.
            if key == "strategy" and row["value"] != "active":
                await db.execute(
                    "UPDATE bot_settings SET value=?, updated_at=? WHERE key=?",
                    ("active", now, "strategy"),
                )
                row = {"value": "active"}
            # DB has a value — apply it to the running config
            _apply_setting_to_config(trading_config, key, row["value"])

    await db.commit()


async def get_all_settings() -> list[dict]:
    """Get all settings with metadata for display."""
    db = await _get_db()
    cursor = await db.execute("SELECT * FROM bot_settings ORDER BY category, key")
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def get_settings_values() -> dict[str, str]:
    """Get setting key->value mapping for quick config reload."""
    db = await _get_db()
    cursor = await db.execute("SELECT key, value FROM bot_settings")
    rows = await cursor.fetchall()
    return {row["key"]: row["value"] for row in rows}


async def update_settings(updates: dict[str, str]):
    """Update multiple settings. Validates against SETTINGS_DEFINITIONS."""
    db = await _get_db()
    now = datetime.now(timezone.utc).isoformat()
    for key, value in updates.items():
        meta = SETTINGS_DEFINITIONS.get(key)
        if not meta:
            continue
        # Validate bounds
        if meta["min_value"] is not None:
            try:
                if float(value) < meta["min_value"]:
                    continue
            except ValueError:
                continue
        if meta["max_value"] is not None:
            try:
                if float(value) > meta["max_value"]:
                    continue
            except ValueError:
                continue
        if meta["value_type"] == "choice" and meta["choices"]:
            valid_choices = json.loads(meta["choices"])
            if value not in valid_choices:
                continue

        await db.execute(
            "UPDATE bot_settings SET value=?, updated_at=? WHERE key=?",
            (str(value), now, key)
        )
    await db.commit()


# ═══════════════════════════════════════════════════════════════════════
# BOT COMMANDS (dashboard -> bot communication)
# ═══════════════════════════════════════════════════════════════════════

async def insert_command(command: str, payload: str = None) -> int:
    """Insert a new command from the dashboard. Returns command ID."""
    db = await _get_db()
    cursor = await db.execute(
        "INSERT INTO bot_commands (command, payload) VALUES (?, ?)",
        (command, payload)
    )
    await db.commit()
    return cursor.lastrowid


async def get_pending_commands() -> list[dict]:
    """Get all pending commands (for bot to process)."""
    db = await _get_db()
    cursor = await db.execute(
        "SELECT * FROM bot_commands WHERE status='pending' ORDER BY created_at ASC"
    )
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def mark_command_executed(command_id: int, result: dict):
    """Mark a command as executed with its result."""
    db = await _get_db()
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "UPDATE bot_commands SET status='executed', result=?, executed_at=? WHERE id=?",
        (json.dumps(result), now, command_id)
    )
    await db.commit()


async def mark_command_failed(command_id: int, error: str):
    """Mark a command as failed."""
    db = await _get_db()
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "UPDATE bot_commands SET status='failed', result=?, executed_at=? WHERE id=?",
        (json.dumps({"error": error}), now, command_id)
    )
    await db.commit()


async def get_recent_commands(limit: int = 20) -> list[dict]:
    """Get recent commands for the Journal tab."""
    db = await _get_db()
    cursor = await db.execute(
        "SELECT * FROM bot_commands ORDER BY created_at DESC LIMIT ?", (limit,)
    )
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


# ═══════════════════════════════════════════════════════════════════════
# LEARNING MODE
# ═══════════════════════════════════════════════════════════════════════

async def insert_journal_entry(entry: dict) -> int:
    db = await _get_db()
    cursor = await db.execute(
        """INSERT INTO learning_journal
           (cycle_number, trades_proposed, trades_executed, trades_skipped,
            skipped_markets, retrospective_json, price_snapshots)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (entry["cycle_number"], entry.get("trades_proposed", 0),
         entry.get("trades_executed", 0), entry.get("trades_skipped", 0),
         entry.get("skipped_markets"), entry.get("retrospective_json"),
         entry.get("price_snapshots"))
    )
    await db.commit()
    return cursor.lastrowid


async def get_journal_entries(limit: int = 50) -> list[dict]:
    db = await _get_db()
    cursor = await db.execute(
        "SELECT * FROM learning_journal ORDER BY created_at DESC LIMIT ?", (limit,)
    )
    rows = await cursor.fetchall()
    return [dict(row) for row in rows]


async def insert_insight(insight: dict) -> int:
    db = await _get_db()
    cursor = await db.execute(
        """INSERT INTO learning_insights (insight_type, description, evidence, proposed_action, severity)
           VALUES (?, ?, ?, ?, ?)""",
        (insight["insight_type"], insight["description"], insight.get("evidence"),
         insight.get("proposed_action"), insight.get("severity", "info"))
    )
    await db.commit()
    return cursor.lastrowid


async def get_active_insights(limit: int = 20) -> list[dict]:
    db = await _get_db()
    cursor = await db.execute(
        "SELECT * FROM learning_insights WHERE status='active' ORDER BY created_at DESC LIMIT ?", (limit,)
    )
    rows = await cursor.fetchall()
    return [dict(row) for row in rows]


async def insert_proposal(proposal: dict) -> int:
    db = await _get_db()
    cursor = await db.execute(
        """INSERT INTO learning_proposals
           (proposal_type, target, current_value, proposed_value, rationale, risk_level)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (proposal["proposal_type"], proposal["target"], proposal.get("current_value"),
         proposal["proposed_value"], proposal["rationale"], proposal.get("risk_level", "moderate"))
    )
    await db.commit()
    return cursor.lastrowid


async def get_pending_proposals() -> list[dict]:
    db = await _get_db()
    cursor = await db.execute(
        "SELECT * FROM learning_proposals WHERE status='pending' ORDER BY created_at DESC"
    )
    rows = await cursor.fetchall()
    return [dict(row) for row in rows]


async def get_proposal_by_id(proposal_id: int) -> dict | None:
    db = await _get_db()
    cursor = await db.execute(
        "SELECT * FROM learning_proposals WHERE id = ?",
        (proposal_id,),
    )
    row = await cursor.fetchone()
    return dict(row) if row else None


async def update_proposal_status(proposal_id: int, status: str):
    db = await _get_db()
    if status == "applied":
        await db.execute(
            "UPDATE learning_proposals SET status=?, applied_at=datetime('now') WHERE id=?",
            (status, proposal_id),
        )
    else:
        await db.execute(
            "UPDATE learning_proposals SET status=? WHERE id=?",
            (status, proposal_id),
        )
    await db.commit()


async def insert_shadow_record(record: dict) -> int:
    db = await _get_db()
    cursor = await db.execute(
        """INSERT INTO learning_shadow
           (cycle_number, market_id, current_decision, shadow_decision, current_params, shadow_params)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (record["cycle_number"], record["market_id"], record.get("current_decision"),
         record.get("shadow_decision"), record.get("current_params"), record.get("shadow_params"))
    )
    await db.commit()
    return cursor.lastrowid


async def get_shadow_records(limit: int = 50) -> list[dict]:
    db = await _get_db()
    cursor = await db.execute(
        "SELECT * FROM learning_shadow ORDER BY created_at DESC LIMIT ?", (limit,)
    )
    rows = await cursor.fetchall()
    return [dict(row) for row in rows]


async def get_all_proposals(limit: int = 50) -> list[dict]:
    """Returns ALL proposals (not filtered by status), ordered by created_at DESC."""
    db = await _get_db()
    cursor = await db.execute(
        "SELECT * FROM learning_proposals ORDER BY created_at DESC LIMIT ?", (limit,)
    )
    rows = await cursor.fetchall()
    return [dict(row) for row in rows]


async def get_journal_entry_by_cycle(cycle_number: int) -> dict | None:
    """Returns a single journal entry dict for a specific cycle number, or None if not found."""
    db = await _get_db()
    cursor = await db.execute(
        "SELECT * FROM learning_journal WHERE cycle_number = ?", (cycle_number,)
    )
    row = await cursor.fetchone()
    return dict(row) if row else None


async def insert_git_change(change: dict) -> int:
    db = await _get_db()
    cursor = await db.execute(
        """INSERT INTO learning_git_changes
           (proposal_id, branch_name, commit_hash, remote_name, push_status,
            justification, files_changed, result)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            change.get("proposal_id"),
            change.get("branch_name", ""),
            change.get("commit_hash"),
            change.get("remote_name", "origin"),
            change.get("push_status", "pending"),
            change.get("justification", ""),
            json.dumps(change.get("files_changed") or []),
            json.dumps(change.get("result") or {}),
        ),
    )
    await db.commit()
    return cursor.lastrowid


async def update_git_change(
    change_id: int,
    *,
    commit_hash: str | None = None,
    push_status: str | None = None,
    result: dict | None = None,
):
    db = await _get_db()
    updates = []
    values = []
    if commit_hash is not None:
        updates.append("commit_hash=?")
        values.append(commit_hash)
    if push_status is not None:
        updates.append("push_status=?")
        values.append(push_status)
    if result is not None:
        updates.append("result=?")
        values.append(json.dumps(result))
    if not updates:
        return
    values.append(change_id)
    await db.execute(
        f"UPDATE learning_git_changes SET {', '.join(updates)} WHERE id=?",
        tuple(values),
    )
    await db.commit()


async def get_git_changes(limit: int = 50) -> list[dict]:
    db = await _get_db()
    cursor = await db.execute(
        "SELECT * FROM learning_git_changes ORDER BY created_at DESC LIMIT ?",
        (limit,),
    )
    rows = await cursor.fetchall()
    items = [dict(r) for r in rows]
    for item in items:
        try:
            item["files_changed"] = json.loads(item.get("files_changed") or "[]")
        except json.JSONDecodeError:
            item["files_changed"] = []
        try:
            item["result"] = json.loads(item.get("result") or "{}")
        except json.JSONDecodeError:
            item["result"] = {}
    return items


async def count_git_changes_today() -> int:
    db = await _get_db()
    cursor = await db.execute(
        """SELECT COUNT(*) AS total
           FROM learning_git_changes
           WHERE date(created_at) = date('now')"""
    )
    row = await cursor.fetchone()
    return int(row["total"] if row else 0)


# ═══════════════════════════════════════════════════════════════════════
# MANAGER CRITIQUES
# ═══════════════════════════════════════════════════════════════════════

async def insert_manager_critique(critique: dict) -> int:
    db = await _get_db()
    cursor = await db.execute(
        """INSERT INTO manager_critiques
           (cycle_number, critique_json, summary,
            trading_quality_score, risk_management_score, strategy_effectiveness_score,
            improvement_areas, code_changes_suggested)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            critique["cycle_number"],
            critique.get("critique_json", ""),
            critique.get("summary", ""),
            critique.get("trading_quality_score"),
            critique.get("risk_management_score"),
            critique.get("strategy_effectiveness_score"),
            critique.get("improvement_areas", ""),
            critique.get("code_changes_suggested", ""),
        ),
    )
    await db.commit()
    return cursor.lastrowid


async def get_critique_by_id(critique_id: int) -> dict | None:
    db = await _get_db()
    cursor = await db.execute(
        "SELECT * FROM manager_critiques WHERE id = ?", (critique_id,)
    )
    row = await cursor.fetchone()
    return dict(row) if row else None


async def get_pending_critiques() -> list[dict]:
    db = await _get_db()
    cursor = await db.execute(
        "SELECT * FROM manager_critiques WHERE status='pending' ORDER BY created_at DESC"
    )
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def get_recent_critiques(limit: int = 20) -> list[dict]:
    db = await _get_db()
    cursor = await db.execute(
        "SELECT * FROM manager_critiques ORDER BY created_at DESC LIMIT ?", (limit,)
    )
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def update_critique_status(critique_id: int, status: str, **kwargs):
    db = await _get_db()
    now = datetime.now(timezone.utc).isoformat()
    updates = ["status=?"]
    values = [status]

    if status in ("approved", "rejected"):
        updates.append("reviewed_at=?")
        values.append(now)
    if status == "deployed":
        updates.append("deployed_at=?")
        values.append(now)

    for key in ("developer_result", "branch_name", "commit_hash", "deploy_status", "user_feedback"):
        if key in kwargs:
            updates.append(f"{key}=?")
            values.append(kwargs[key] if not isinstance(kwargs[key], dict) else json.dumps(kwargs[key]))

    values.append(critique_id)
    await db.execute(
        f"UPDATE manager_critiques SET {', '.join(updates)} WHERE id=?",
        tuple(values),
    )
    await db.commit()


async def get_deploy_pending_critiques() -> list[dict]:
    db = await _get_db()
    cursor = await db.execute(
        "SELECT * FROM manager_critiques WHERE status='deploy_pending'"
    )
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


# ═══════════════════════════════════════════════════════════════════════
# STRATEGIST ASSESSMENTS
# ═══════════════════════════════════════════════════════════════════════

async def insert_strategist_assessment(assessment: dict) -> int:
    db = await _get_db()
    cursor = await db.execute(
        """INSERT INTO strategist_assessments
           (assessment_json, summary, market_regime, regime_confidence,
            allocation_score, diversification_score,
            category_allocation, recommendations, strategic_insights)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            assessment.get("assessment_json", ""),
            assessment.get("summary", ""),
            assessment.get("market_regime", "normal"),
            assessment.get("regime_confidence", 0.5),
            assessment.get("allocation_score"),
            assessment.get("diversification_score"),
            assessment.get("category_allocation", ""),
            assessment.get("recommendations", ""),
            assessment.get("strategic_insights", ""),
        ),
    )
    await db.commit()
    return cursor.lastrowid


async def get_assessment_by_id(assessment_id: int) -> dict | None:
    db = await _get_db()
    cursor = await db.execute(
        "SELECT * FROM strategist_assessments WHERE id = ?", (assessment_id,)
    )
    row = await cursor.fetchone()
    return dict(row) if row else None


async def get_recent_assessments(limit: int = 20) -> list[dict]:
    db = await _get_db()
    cursor = await db.execute(
        "SELECT * FROM strategist_assessments ORDER BY created_at DESC LIMIT ?", (limit,)
    )
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def get_latest_assessment() -> dict | None:
    db = await _get_db()
    cursor = await db.execute(
        "SELECT * FROM strategist_assessments ORDER BY created_at DESC LIMIT 1"
    )
    row = await cursor.fetchone()
    return dict(row) if row else None


# ═══════════════════════════════════════════════════════════════════════
# RISK OFFICER REVIEWS
# ═══════════════════════════════════════════════════════════════════════

async def insert_risk_officer_review(review: dict, cycle_number: int | None = None) -> int:
    db = await _get_db()
    cursor = await db.execute(
        """INSERT INTO risk_officer_reviews
           (cycle_number, review_json, portfolio_risk_summary,
            trades_reviewed, trades_flagged, trades_rejected,
            parameter_recommendations)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            cycle_number,
            review.get("review_json", json.dumps(review)),
            review.get("portfolio_risk_summary", ""),
            review.get("trades_reviewed", 0),
            review.get("trades_flagged", 0),
            review.get("trades_rejected", 0),
            json.dumps(review.get("parameter_recommendations") or []),
        ),
    )
    await db.commit()
    return cursor.lastrowid


async def get_recent_risk_reviews(limit: int = 20) -> list[dict]:
    db = await _get_db()
    cursor = await db.execute(
        "SELECT * FROM risk_officer_reviews ORDER BY created_at DESC LIMIT ?", (limit,)
    )
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def get_risk_review_by_id(review_id: int) -> dict | None:
    db = await _get_db()
    cursor = await db.execute(
        "SELECT * FROM risk_officer_reviews WHERE id = ?", (review_id,)
    )
    row = await cursor.fetchone()
    return dict(row) if row else None


# ═══════════════════════════════════════════════════════════════════════
# CONVERSATIONS
# ═══════════════════════════════════════════════════════════════════════

async def insert_conversation_turn(turn: dict) -> int:
    db = await _get_db()
    cursor = await db.execute(
        """INSERT INTO conversations (source, role, agent_name, message, action_taken)
           VALUES (?, ?, ?, ?, ?)""",
        (
            turn["source"],
            turn["role"],
            turn.get("agent_name", "general"),
            turn["message"],
            turn.get("action_taken"),
        ),
    )
    await db.commit()
    return cursor.lastrowid


async def get_recent_conversations(source: str, limit: int = 20) -> list[dict]:
    db = await _get_db()
    cursor = await db.execute(
        """SELECT * FROM conversations
           WHERE source = ?
           ORDER BY created_at DESC LIMIT ?""",
        (source, limit),
    )
    rows = await cursor.fetchall()
    items = [dict(r) for r in rows]
    items.reverse()  # chronological order
    return items


async def get_all_conversations(limit: int = 50) -> list[dict]:
    db = await _get_db()
    cursor = await db.execute(
        "SELECT * FROM conversations ORDER BY created_at DESC LIMIT ?", (limit,)
    )
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


# ═══════════════════════════════════════════════════════════════════════
# FILE CHANGE AUDIT
# ═══════════════════════════════════════════════════════════════════════

async def insert_file_change_audit(entry: dict) -> int:
    db = await _get_db()
    cursor = await db.execute(
        """INSERT INTO file_change_audit
           (file_path, change_type, tier, agent_name, reason, diff_summary, backup_path, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            entry["file_path"],
            entry["change_type"],
            entry["tier"],
            entry["agent_name"],
            entry.get("reason"),
            entry.get("diff_summary"),
            entry.get("backup_path"),
            entry.get("status", "pending"),
        ),
    )
    await db.commit()
    return cursor.lastrowid


async def get_pending_file_changes() -> list[dict]:
    db = await _get_db()
    cursor = await db.execute(
        "SELECT * FROM file_change_audit WHERE status='pending' ORDER BY created_at DESC"
    )
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def update_file_change_status(change_id: int, status: str, **kwargs):
    db = await _get_db()
    updates = ["status=?"]
    values = [status]
    for key in ("backup_path", "diff_summary"):
        if key in kwargs:
            updates.append(f"{key}=?")
            values.append(kwargs[key])
    values.append(change_id)
    await db.execute(
        f"UPDATE file_change_audit SET {', '.join(updates)} WHERE id=?",
        tuple(values),
    )
    await db.commit()


async def get_recent_file_changes(limit: int = 50) -> list[dict]:
    db = await _get_db()
    cursor = await db.execute(
        "SELECT * FROM file_change_audit ORDER BY created_at DESC LIMIT ?", (limit,)
    )
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


# ═══════════════════════════════════════════════════════════════════════
# MM QUOTES
# ═══════════════════════════════════════════════════════════════════════

async def insert_mm_quote(quote: dict) -> int:
    db = await _get_db()
    cursor = await db.execute(
        """INSERT INTO mm_quotes
           (market_id, token_id, bid_order_id, ask_order_id,
            bid_price, ask_price, mid_price, size, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (quote["market_id"], quote["token_id"],
         quote.get("bid_order_id"), quote.get("ask_order_id"),
         quote["bid_price"], quote["ask_price"],
         quote.get("mid_price"), quote["size"],
         quote.get("status", "active"))
    )
    await db.commit()
    return cursor.lastrowid


async def update_mm_quote_status(quote_id: int, status: str, **kwargs):
    db = await _get_db()
    now = datetime.now(timezone.utc).isoformat()
    updates = ["status=?", "updated_at=?"]
    values = [status, now]
    for key in ("bid_order_id", "ask_order_id"):
        if key in kwargs:
            updates.append(f"{key}=?")
            values.append(kwargs[key])
    values.append(quote_id)
    await db.execute(
        f"UPDATE mm_quotes SET {', '.join(updates)} WHERE id=?",
        tuple(values),
    )
    await db.commit()


async def get_active_mm_quotes() -> list[dict]:
    db = await _get_db()
    cursor = await db.execute(
        "SELECT * FROM mm_quotes WHERE status='active' ORDER BY created_at DESC"
    )
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def get_mm_quotes_by_market(market_id: str) -> list[dict]:
    db = await _get_db()
    cursor = await db.execute(
        "SELECT * FROM mm_quotes WHERE market_id=? AND status='active'",
        (market_id,)
    )
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def cancel_mm_quotes_for_market(market_id: str):
    db = await _get_db()
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "UPDATE mm_quotes SET status='cancelled', updated_at=? WHERE market_id=? AND status='active'",
        (now, market_id)
    )
    await db.commit()


# ═══════════════════════════════════════════════════════════════════════
# MM INVENTORY
# ═══════════════════════════════════════════════════════════════════════

async def upsert_mm_inventory(market_id: str, token_id: str,
                               position_delta: float, fill_price: float):
    """Update inventory after a fill. Positive delta = bought, negative = sold."""
    db = await _get_db()
    now = datetime.now(timezone.utc).isoformat()
    cursor = await db.execute(
        "SELECT * FROM mm_inventory WHERE market_id=? AND token_id=?",
        (market_id, token_id)
    )
    row = await cursor.fetchone()

    if row is None:
        await db.execute(
            """INSERT INTO mm_inventory
               (market_id, token_id, net_position, avg_entry_price, updated_at)
               VALUES (?, ?, ?, ?, ?)""",
            (market_id, token_id, position_delta, fill_price, now)
        )
    else:
        old_pos = float(row["net_position"])
        old_avg = float(row["avg_entry_price"])
        new_pos = old_pos + position_delta

        if position_delta > 0 and old_pos >= 0:
            # Adding to long: weighted avg
            total_cost = old_avg * old_pos + fill_price * position_delta
            new_avg = total_cost / new_pos if new_pos > 0 else fill_price
        elif position_delta < 0 and old_pos > 0:
            # Reducing long: realize PnL, keep avg
            realized = abs(position_delta) * (fill_price - old_avg)
            old_realized = float(row["realized_pnl"])
            await db.execute(
                """UPDATE mm_inventory SET net_position=?, realized_pnl=?, updated_at=?
                   WHERE market_id=? AND token_id=?""",
                (new_pos, old_realized + realized, now, market_id, token_id)
            )
            await db.commit()
            return
            new_avg = old_avg
        else:
            new_avg = fill_price if abs(new_pos) > 0.001 else 0

        await db.execute(
            """UPDATE mm_inventory SET net_position=?, avg_entry_price=?, updated_at=?
               WHERE market_id=? AND token_id=?""",
            (new_pos, new_avg, now, market_id, token_id)
        )
    await db.commit()


async def get_mm_inventory(market_id: str = None) -> list[dict]:
    db = await _get_db()
    if market_id:
        cursor = await db.execute(
            "SELECT * FROM mm_inventory WHERE market_id=?", (market_id,)
        )
    else:
        cursor = await db.execute("SELECT * FROM mm_inventory WHERE ABS(net_position) > 0.001")
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def get_mm_total_exposure() -> float:
    db = await _get_db()
    cursor = await db.execute(
        "SELECT COALESCE(SUM(ABS(net_position) * avg_entry_price), 0) as exposure FROM mm_inventory"
    )
    row = await cursor.fetchone()
    return float(row["exposure"])


async def reset_mm_inventory(market_id: str, token_id: str):
    db = await _get_db()
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        """UPDATE mm_inventory SET net_position=0, avg_entry_price=0,
           unrealized_pnl=0, updated_at=? WHERE market_id=? AND token_id=?""",
        (now, market_id, token_id)
    )
    await db.commit()


# ═══════════════════════════════════════════════════════════════════════
# MM FILLS
# ═══════════════════════════════════════════════════════════════════════

async def insert_mm_fill(fill: dict) -> int:
    db = await _get_db()
    cursor = await db.execute(
        """INSERT INTO mm_fills
           (quote_id, order_id, side, price, size, fee, mid_at_fill)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (fill.get("quote_id"), fill["order_id"], fill["side"],
         fill["price"], fill["size"], fill.get("fee", 0),
         fill.get("mid_at_fill"))
    )
    await db.commit()
    return cursor.lastrowid


async def update_mm_fill_adverse_selection(fill_id: int, mid_at_30s: float = None,
                                            mid_at_120s: float = None):
    db = await _get_db()
    updates = []
    values = []
    if mid_at_30s is not None:
        updates.append("mid_at_30s=?")
        values.append(mid_at_30s)
    if mid_at_120s is not None:
        updates.append("mid_at_120s=?")
        values.append(mid_at_120s)
    if not updates:
        return
    values.append(fill_id)
    await db.execute(
        f"UPDATE mm_fills SET {', '.join(updates)} WHERE id=?",
        tuple(values),
    )
    await db.commit()


async def get_recent_mm_fills(limit: int = 100) -> list[dict]:
    db = await _get_db()
    cursor = await db.execute(
        "SELECT * FROM mm_fills ORDER BY created_at DESC LIMIT ?", (limit,)
    )
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def get_recent_mm_quotes(limit: int = 500) -> list[dict]:
    db = await _get_db()
    cursor = await db.execute(
        "SELECT * FROM mm_quotes ORDER BY created_at DESC LIMIT ?", (limit,)
    )
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def get_pending_adverse_selection_fills(window_seconds: int = 120) -> list[dict]:
    """Get fills that need adverse selection measurement (mid_at_30s or mid_at_120s is NULL)."""
    db = await _get_db()
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=window_seconds + 30)).isoformat()
    cursor = await db.execute(
        """SELECT * FROM mm_fills
           WHERE (mid_at_30s IS NULL OR mid_at_120s IS NULL)
           AND created_at >= ?
           ORDER BY created_at ASC""",
        (cutoff,)
    )
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


# ═══════════════════════════════════════════════════════════════════════
# MM ROUND TRIPS
# ═══════════════════════════════════════════════════════════════════════

async def insert_mm_round_trip(rt: dict) -> int:
    db = await _get_db()
    cursor = await db.execute(
        """INSERT INTO mm_round_trips
           (market_id, token_id, entry_fill_id, exit_fill_id,
            entry_price, exit_price, size, gross_pnl, net_pnl, hold_seconds)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (rt["market_id"], rt["token_id"],
         rt.get("entry_fill_id"), rt.get("exit_fill_id"),
         rt["entry_price"], rt["exit_price"], rt["size"],
         rt["gross_pnl"], rt["net_pnl"], rt.get("hold_seconds"))
    )
    await db.commit()
    return cursor.lastrowid


async def get_mm_round_trips(days: int = 7) -> list[dict]:
    db = await _get_db()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    cursor = await db.execute(
        "SELECT * FROM mm_round_trips WHERE created_at >= ? ORDER BY created_at DESC",
        (cutoff,)
    )
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


# ═══════════════════════════════════════════════════════════════════════
# MM DAILY METRICS
# ═══════════════════════════════════════════════════════════════════════

async def upsert_mm_daily_metrics(date_str: str, metrics: dict):
    db = await _get_db()
    await db.execute(
        """INSERT INTO mm_daily_metrics
           (date, markets_quoted, quotes_placed, fills_count, round_trips,
            spread_capture_rate, fill_quality_avg, adverse_selection_avg,
            pnl_gross, pnl_net, max_inventory, inventory_turns,
            profit_factor, sharpe_7d, portfolio_value)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(date) DO UPDATE SET
           markets_quoted=excluded.markets_quoted,
           quotes_placed=excluded.quotes_placed,
           fills_count=excluded.fills_count,
           round_trips=excluded.round_trips,
           spread_capture_rate=excluded.spread_capture_rate,
           fill_quality_avg=excluded.fill_quality_avg,
           adverse_selection_avg=excluded.adverse_selection_avg,
           pnl_gross=excluded.pnl_gross,
           pnl_net=excluded.pnl_net,
           max_inventory=excluded.max_inventory,
           inventory_turns=excluded.inventory_turns,
           profit_factor=excluded.profit_factor,
           sharpe_7d=excluded.sharpe_7d,
           portfolio_value=excluded.portfolio_value""",
        (date_str, metrics.get("markets_quoted", 0),
         metrics.get("quotes_placed", 0), metrics.get("fills_count", 0),
         metrics.get("round_trips", 0), metrics.get("spread_capture_rate", 0),
         metrics.get("fill_quality_avg", 0), metrics.get("adverse_selection_avg", 0),
         metrics.get("pnl_gross", 0), metrics.get("pnl_net", 0),
         metrics.get("max_inventory", 0), metrics.get("inventory_turns", 0),
         metrics.get("profit_factor", 0), metrics.get("sharpe_7d", 0),
         metrics.get("portfolio_value", 0))
    )
    await db.commit()


async def get_mm_daily_metrics(days: int = 30) -> list[dict]:
    db = await _get_db()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    cursor = await db.execute(
        "SELECT * FROM mm_daily_metrics WHERE date >= ? ORDER BY date DESC",
        (cutoff,)
    )
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


# ═══════════════════════════════════════════════════════════════════════
# CD SIGNALS
# ═══════════════════════════════════════════════════════════════════════

async def insert_cd_signal(signal: dict) -> int:
    db = await _get_db()
    cursor = await db.execute(
        """INSERT INTO cd_signals
           (market_id, token_id, coin, strike, expiry_days, spot_price,
            vol_ewma, p_model, p_market, edge_pts, confirmation_count,
            action, size_usdc, order_id, ai_validation)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (signal["market_id"], signal.get("token_id"),
         signal["coin"], signal["strike"], signal["expiry_days"],
         signal["spot_price"], signal["vol_ewma"],
         signal["p_model"], signal["p_market"], signal["edge_pts"],
         signal.get("confirmation_count", 1),
         signal.get("action", "none"),
         signal.get("size_usdc"), signal.get("order_id"),
         signal.get("ai_validation"))
    )
    await db.commit()
    return cursor.lastrowid


async def get_recent_cd_signals(limit: int = 50) -> list[dict]:
    db = await _get_db()
    cursor = await db.execute(
        "SELECT * FROM cd_signals ORDER BY created_at DESC LIMIT ?", (limit,)
    )
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def get_cd_signal_confirmation(market_id: str, min_edge: float) -> int:
    """Count consecutive recent signals with edge >= min_edge for a market."""
    db = await _get_db()
    cursor = await db.execute(
        """SELECT edge_pts FROM cd_signals
           WHERE market_id=? ORDER BY created_at DESC LIMIT 10""",
        (market_id,)
    )
    rows = await cursor.fetchall()
    count = 0
    for row in rows:
        if float(row["edge_pts"]) >= min_edge:
            count += 1
        else:
            break
    return count


# ═══════════════════════════════════════════════════════════════════════
# CD POSITIONS
# ═══════════════════════════════════════════════════════════════════════

async def insert_cd_position(position: dict) -> int:
    """Insert a new CD position after a successful BUY fill."""
    db = await _get_db()
    cursor = await db.execute(
        """INSERT INTO cd_positions
           (market_id, token_id, coin, strike, direction,
            entry_price, shares, expiry_days, order_id, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'open')
           ON CONFLICT(market_id, token_id) DO UPDATE SET
           shares = cd_positions.shares + excluded.shares,
           entry_price = (cd_positions.entry_price * cd_positions.shares
                          + excluded.entry_price * excluded.shares)
                         / (cd_positions.shares + excluded.shares),
           expiry_days = COALESCE(excluded.expiry_days, cd_positions.expiry_days),
           status = 'open'""",
        (position["market_id"], position["token_id"],
         position["coin"], position["strike"], position["direction"],
         position["entry_price"], position["shares"],
         position.get("expiry_days"),
         position.get("order_id"))
    )
    await db.commit()
    return cursor.lastrowid


async def get_open_cd_positions() -> list[dict]:
    """List all open CD positions."""
    db = await _get_db()
    cursor = await db.execute(
        "SELECT * FROM cd_positions WHERE status='open'"
    )
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def close_cd_position(
    market_id: str,
    token_id: str,
    exit_price: float,
    exit_reason: str,
    exit_order_id: str | None = None,
) -> dict | None:
    """Close a CD position and record exit details. Returns position data or None."""
    db = await _get_db()
    cursor = await db.execute(
        "SELECT * FROM cd_positions WHERE market_id=? AND token_id=? AND status='open'",
        (market_id, token_id)
    )
    row = await cursor.fetchone()
    if not row:
        return None

    pos = dict(row)
    entry_price = float(pos["entry_price"])
    shares = float(pos["shares"])
    pnl = (exit_price - entry_price) * shares

    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        """UPDATE cd_positions
           SET status=?, exit_price=?, exit_reason=?, exit_order_id=?,
               pnl_realized=?, closed_at=?
           WHERE market_id=? AND token_id=? AND status='open'""",
        (exit_reason, exit_price, exit_reason, exit_order_id,
         round(pnl, 4), now, market_id, token_id)
    )
    await db.commit()

    pos["exit_price"] = exit_price
    pos["exit_reason"] = exit_reason
    pos["pnl_realized"] = round(pnl, 4)
    return pos


# ═══════════════════════════════════════════════════════════════════════
# CD TRADE ANALYSES
# ═══════════════════════════════════════════════════════════════════════

async def insert_cd_trade_analysis(analysis: dict) -> int:
    """Insert a new CD trade analysis from Claude Opus."""
    db = await _get_db()
    cursor = await db.execute(
        """INSERT INTO cd_trade_analyses
           (analysis_type, signals_analyzed, positions_analyzed,
            accuracy_score, entry_quality_score, exit_quality_score,
            model_fitness_score, overall_score,
            parameter_suggestions, insights, summary, raw_response)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (analysis.get("analysis_type", "periodic"),
         analysis.get("signals_analyzed", 0),
         analysis.get("positions_analyzed", 0),
         analysis.get("accuracy_score"),
         analysis.get("entry_quality_score"),
         analysis.get("exit_quality_score"),
         analysis.get("model_fitness_score"),
         analysis.get("overall_score"),
         json.dumps(analysis.get("parameter_suggestions", {})),
         json.dumps(analysis.get("insights", [])),
         analysis.get("summary", ""),
         analysis.get("raw_response", ""))
    )
    await db.commit()
    return cursor.lastrowid


async def get_recent_cd_analyses(limit: int = 10) -> list[dict]:
    """Get the most recent CD trade analyses."""
    db = await _get_db()
    cursor = await db.execute(
        "SELECT * FROM cd_trade_analyses ORDER BY created_at DESC LIMIT ?",
        (limit,)
    )
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def get_closed_cd_positions(limit: int = 100) -> list[dict]:
    """Get recently closed CD positions for analysis."""
    db = await _get_db()
    cursor = await db.execute(
        "SELECT * FROM cd_positions WHERE status != 'open' ORDER BY closed_at DESC LIMIT ?",
        (limit,)
    )
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]
