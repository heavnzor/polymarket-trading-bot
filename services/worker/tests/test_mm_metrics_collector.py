"""Tests for mm/metrics_collector.py — adverse selection + daily metrics."""

import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch, AsyncMock

import pytest

WORKER_DIR = Path(__file__).resolve().parents[1]
if str(WORKER_DIR) not in sys.path:
    sys.path.insert(0, str(WORKER_DIR))

from mm.metrics_collector import MetricsCollector


def _make_fill(
    fill_id: int = 1,
    quote_id: int = 1,
    side: str = "BUY",
    price: float = 0.50,
    size: float = 10.0,
    mid_at_fill: float = 0.52,
    mid_at_30s: float = None,
    mid_at_120s: float = None,
    adverse_selection: float = None,
    age_seconds: float = 60,
) -> dict:
    """Create a fake fill dict matching the schema used by MetricsCollector."""
    created_at = (datetime.now(timezone.utc) - timedelta(seconds=age_seconds)).isoformat()
    return {
        "id": fill_id,
        "quote_id": quote_id,
        "order_id": f"order-{fill_id}",
        "side": side,
        "price": price,
        "size": size,
        "fee": 0.0,
        "mid_at_fill": mid_at_fill,
        "mid_at_30s": mid_at_30s,
        "mid_at_120s": mid_at_120s,
        "adverse_selection": adverse_selection,
        "created_at": created_at,
    }


@pytest.fixture
def mock_client():
    client = MagicMock()
    client.get_book_summary.return_value = {"mid": 0.55, "spread": 0.04}
    return client


@pytest.fixture
def collector(mock_client):
    return MetricsCollector(client=mock_client)


# ────────────────────────────────────────────────────────────
# measure_adverse_selection
# ────────────────────────────────────────────────────────────

class TestMeasureAdverseSelection:
    """Tests for measure_adverse_selection()."""

    @pytest.mark.asyncio
    async def test_skips_when_no_client(self):
        """No client => early return, no crash."""
        collector = MetricsCollector(client=None)
        await collector.measure_adverse_selection()  # Should not raise

    @pytest.mark.asyncio
    async def test_skips_when_no_pending(self, collector):
        """Empty pending list => no update calls."""
        with patch("mm.metrics_collector.store") as mock_store:
            mock_store.get_pending_adverse_selection_fills = AsyncMock(return_value=[])
            await collector.measure_adverse_selection()
            mock_store.update_mm_fill_adverse_selection.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_when_get_pending_raises(self, collector):
        """Exception in get_pending_adverse_selection_fills => graceful return."""
        with patch("mm.metrics_collector.store") as mock_store:
            mock_store.get_pending_adverse_selection_fills = AsyncMock(
                side_effect=Exception("db error")
            )
            await collector.measure_adverse_selection()  # Should not raise

    @pytest.mark.asyncio
    async def test_skips_fill_with_no_created_at(self, collector):
        """Fill missing created_at => skip it."""
        fill = _make_fill(age_seconds=45, mid_at_30s=None)
        fill["created_at"] = ""

        with patch("mm.metrics_collector.store") as mock_store:
            mock_store.get_pending_adverse_selection_fills = AsyncMock(return_value=[fill])
            mock_store.update_mm_fill_adverse_selection = AsyncMock()
            await collector.measure_adverse_selection()
            mock_store.update_mm_fill_adverse_selection.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_fill_too_young_for_30s(self, collector):
        """Fill only 20s old, needs 30s => no measurement."""
        fill = _make_fill(age_seconds=20, mid_at_30s=None)

        with patch("mm.metrics_collector.store") as mock_store:
            mock_store.get_pending_adverse_selection_fills = AsyncMock(return_value=[fill])
            mock_store.update_mm_fill_adverse_selection = AsyncMock()

            # Token lookup mock (needed even if not reached for this fill)
            mock_db = MagicMock()
            mock_cursor = MagicMock()
            mock_cursor.fetchone = AsyncMock(return_value={"token_id": "tok-1"})
            mock_db.execute = AsyncMock(return_value=mock_cursor)
            mock_store._get_db = AsyncMock(return_value=mock_db)

            await collector.measure_adverse_selection()
            mock_store.update_mm_fill_adverse_selection.assert_not_called()

    @pytest.mark.asyncio
    async def test_measures_mid_at_30s(self, collector, mock_client):
        """Fill 45s old, mid_at_30s=None => should measure mid_at_30s."""
        fill = _make_fill(age_seconds=45, mid_at_30s=None)

        with patch("mm.metrics_collector.store") as mock_store:
            mock_store.get_pending_adverse_selection_fills = AsyncMock(return_value=[fill])
            mock_store.update_mm_fill_adverse_selection = AsyncMock()

            mock_db = MagicMock()
            mock_cursor = MagicMock()
            mock_cursor.fetchone = AsyncMock(return_value={"token_id": "tok-1"})
            mock_db.execute = AsyncMock(return_value=mock_cursor)
            mock_store._get_db = AsyncMock(return_value=mock_db)

            await collector.measure_adverse_selection()

            mock_store.update_mm_fill_adverse_selection.assert_called_once()
            call_kwargs = mock_store.update_mm_fill_adverse_selection.call_args
            # Called as: update_mm_fill_adverse_selection(fill_id, mid_at_30s=mid)
            assert call_kwargs[0][0] == 1  # fill_id
            assert call_kwargs[1]["mid_at_30s"] == 0.55  # mid from mock_client

    @pytest.mark.asyncio
    async def test_measures_mid_at_120s_and_computes_as(self, collector, mock_client):
        """Fill 150s old, mid_at_30s already set, mid_at_120s=None => measure + compute AS."""
        fill = _make_fill(
            age_seconds=150, side="BUY", price=0.50, mid_at_fill=0.52,
            mid_at_30s=0.51, mid_at_120s=None,
        )

        with patch("mm.metrics_collector.store") as mock_store, \
             patch("mm.metrics_collector.mm_metrics") as mock_mm_metrics:
            mock_store.get_pending_adverse_selection_fills = AsyncMock(return_value=[fill])
            mock_store.update_mm_fill_adverse_selection = AsyncMock()

            mock_db = MagicMock()
            mock_cursor = MagicMock()
            mock_cursor.fetchone = AsyncMock(return_value={"token_id": "tok-1"})
            mock_db.execute = AsyncMock(return_value=mock_cursor)
            mock_db.commit = AsyncMock()
            mock_store._get_db = AsyncMock(return_value=mock_db)

            mock_mm_metrics.adverse_selection.return_value = 12.5

            await collector.measure_adverse_selection()

            # Should update mid_at_120s
            mock_store.update_mm_fill_adverse_selection.assert_called_once_with(
                1, mid_at_120s=0.55
            )
            # Should compute AS via mm_metrics.adverse_selection
            mock_mm_metrics.adverse_selection.assert_called_once_with(
                fill_price=0.50,
                mid_at_fill=0.52,
                mid_at_later=0.55,
                side="BUY",
            )
            # Should store AS value via _update_as_value (direct DB update)
            # Check the db.execute call for the AS UPDATE statement
            update_calls = [
                c for c in mock_db.execute.await_args_list
                if "adverse_selection" in str(c)
            ]
            assert len(update_calls) == 1

    @pytest.mark.asyncio
    async def test_skips_fill_with_no_quote_id(self, collector):
        """Fill with quote_id=None => _get_token_id returns None => skip."""
        fill = _make_fill(age_seconds=45, mid_at_30s=None)
        fill["quote_id"] = None

        with patch("mm.metrics_collector.store") as mock_store:
            mock_store.get_pending_adverse_selection_fills = AsyncMock(return_value=[fill])
            mock_store.update_mm_fill_adverse_selection = AsyncMock()
            await collector.measure_adverse_selection()
            mock_store.update_mm_fill_adverse_selection.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_when_token_id_not_found(self, collector):
        """Token lookup returns no row => skip fill."""
        fill = _make_fill(age_seconds=45, mid_at_30s=None)

        with patch("mm.metrics_collector.store") as mock_store:
            mock_store.get_pending_adverse_selection_fills = AsyncMock(return_value=[fill])
            mock_store.update_mm_fill_adverse_selection = AsyncMock()

            mock_db = MagicMock()
            mock_cursor = MagicMock()
            mock_cursor.fetchone = AsyncMock(return_value=None)  # No row
            mock_db.execute = AsyncMock(return_value=mock_cursor)
            mock_store._get_db = AsyncMock(return_value=mock_db)

            await collector.measure_adverse_selection()
            mock_store.update_mm_fill_adverse_selection.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_when_book_summary_returns_none(self, collector, mock_client):
        """get_book_summary returns None => mid is None => skip update."""
        mock_client.get_book_summary.return_value = None
        fill = _make_fill(age_seconds=45, mid_at_30s=None)

        with patch("mm.metrics_collector.store") as mock_store:
            mock_store.get_pending_adverse_selection_fills = AsyncMock(return_value=[fill])
            mock_store.update_mm_fill_adverse_selection = AsyncMock()

            mock_db = MagicMock()
            mock_cursor = MagicMock()
            mock_cursor.fetchone = AsyncMock(return_value={"token_id": "tok-1"})
            mock_db.execute = AsyncMock(return_value=mock_cursor)
            mock_store._get_db = AsyncMock(return_value=mock_db)

            await collector.measure_adverse_selection()
            mock_store.update_mm_fill_adverse_selection.assert_not_called()

    @pytest.mark.asyncio
    async def test_token_id_cache(self, collector, mock_client):
        """Token ID should be cached after first lookup — DB hit only once per quote_id."""
        fill1 = _make_fill(fill_id=1, age_seconds=45, mid_at_30s=None, quote_id=1)
        fill2 = _make_fill(fill_id=2, age_seconds=60, mid_at_30s=None, quote_id=1)

        with patch("mm.metrics_collector.store") as mock_store:
            mock_store.get_pending_adverse_selection_fills = AsyncMock(
                return_value=[fill1, fill2]
            )
            mock_store.update_mm_fill_adverse_selection = AsyncMock()

            mock_db = MagicMock()
            mock_cursor = MagicMock()
            mock_cursor.fetchone = AsyncMock(return_value={"token_id": "tok-1"})
            mock_db.execute = AsyncMock(return_value=mock_cursor)
            mock_store._get_db = AsyncMock(return_value=mock_db)

            await collector.measure_adverse_selection()

            # DB execute for token lookup should be called only once (cached second time)
            assert mock_db.execute.call_count == 1

    @pytest.mark.asyncio
    async def test_multiple_fills_different_quote_ids(self, collector, mock_client):
        """Different quote_ids => separate DB lookups, separate cache entries."""
        fill1 = _make_fill(fill_id=1, age_seconds=45, mid_at_30s=None, quote_id=10)
        fill2 = _make_fill(fill_id=2, age_seconds=60, mid_at_30s=None, quote_id=20)

        with patch("mm.metrics_collector.store") as mock_store:
            mock_store.get_pending_adverse_selection_fills = AsyncMock(
                return_value=[fill1, fill2]
            )
            mock_store.update_mm_fill_adverse_selection = AsyncMock()

            mock_db = MagicMock()
            mock_cursor = MagicMock()
            mock_cursor.fetchone = AsyncMock(return_value={"token_id": "tok-1"})
            mock_db.execute = AsyncMock(return_value=mock_cursor)
            mock_store._get_db = AsyncMock(return_value=mock_db)

            await collector.measure_adverse_selection()

            # Two distinct quote_ids => two DB lookups
            assert mock_db.execute.call_count == 2
            # Both fills should have been updated
            assert mock_store.update_mm_fill_adverse_selection.call_count == 2

    @pytest.mark.asyncio
    async def test_fill_old_enough_for_both_30s_and_120s(self, collector, mock_client):
        """Fill 150s old with mid_at_30s=None and mid_at_120s=None => measures both."""
        fill = _make_fill(age_seconds=150, mid_at_30s=None, mid_at_120s=None)

        with patch("mm.metrics_collector.store") as mock_store, \
             patch("mm.metrics_collector.mm_metrics") as mock_mm_metrics:
            mock_store.get_pending_adverse_selection_fills = AsyncMock(return_value=[fill])
            mock_store.update_mm_fill_adverse_selection = AsyncMock()

            mock_db = MagicMock()
            mock_cursor = MagicMock()
            mock_cursor.fetchone = AsyncMock(return_value={"token_id": "tok-1"})
            mock_db.execute = AsyncMock(return_value=mock_cursor)
            mock_db.commit = AsyncMock()
            mock_store._get_db = AsyncMock(return_value=mock_db)

            mock_mm_metrics.adverse_selection.return_value = 5.0

            await collector.measure_adverse_selection()

            # Should be called twice: once for mid_at_30s, once for mid_at_120s
            assert mock_store.update_mm_fill_adverse_selection.call_count == 2


# ────────────────────────────────────────────────────────────
# compute_daily_metrics
# ────────────────────────────────────────────────────────────

class TestComputeDailyMetrics:
    """Tests for compute_daily_metrics()."""

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_fills(self, collector):
        """No fills at all => empty dict."""
        with patch("mm.metrics_collector.store") as mock_store:
            mock_store.get_recent_mm_fills = AsyncMock(return_value=[])
            result = await collector.compute_daily_metrics()
            assert result == {}

    @pytest.mark.asyncio
    async def test_returns_empty_when_get_fills_raises(self, collector):
        """Exception in get_recent_mm_fills => empty dict."""
        with patch("mm.metrics_collector.store") as mock_store:
            mock_store.get_recent_mm_fills = AsyncMock(
                side_effect=Exception("db error")
            )
            result = await collector.compute_daily_metrics()
            assert result == {}

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_today_fills(self, collector):
        """Fills exist but none from today => empty dict."""
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        fill = _make_fill(age_seconds=100)
        fill["created_at"] = f"{yesterday}T12:00:00+00:00"

        with patch("mm.metrics_collector.store") as mock_store:
            mock_store.get_recent_mm_fills = AsyncMock(return_value=[fill])
            result = await collector.compute_daily_metrics()
            assert result == {}

    @pytest.mark.asyncio
    async def test_computes_basic_metrics(self, collector):
        """Two fills today => fills_count, pnl, fill_quality, AS avg, etc."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        fills = [
            _make_fill(
                fill_id=1, side="BUY", price=0.48, size=10.0,
                mid_at_fill=0.50, adverse_selection=10.0, age_seconds=100,
            ),
            _make_fill(
                fill_id=2, side="SELL", price=0.52, size=10.0,
                mid_at_fill=0.50, adverse_selection=-5.0, age_seconds=50,
            ),
        ]
        for f in fills:
            f["created_at"] = f"{today}T12:00:00+00:00"

        with patch("mm.metrics_collector.store") as mock_store:
            mock_store.get_recent_mm_fills = AsyncMock(return_value=fills)
            mock_store.get_recent_mm_quotes = AsyncMock(return_value=[])
            mock_store.get_mm_inventory = AsyncMock(return_value=[])
            mock_store.upsert_mm_daily_metrics = AsyncMock()

            # Mock for _compute_rolling_sharpe
            mock_db = MagicMock()
            mock_cursor = MagicMock()
            mock_cursor.fetchall = AsyncMock(return_value=[])
            mock_db.execute = AsyncMock(return_value=mock_cursor)
            mock_store._get_db = AsyncMock(return_value=mock_db)

            result = await collector.compute_daily_metrics()

            assert result["fills_count"] == 2
            assert "pnl_gross" in result
            assert "pnl_net" in result
            assert "fill_quality_avg" in result
            assert "adverse_selection_avg" in result
            assert "spread_capture_rate" in result
            assert "profit_factor" in result
            assert "max_inventory" in result
            assert "inventory_turns" in result
            assert "sharpe_7d" in result

            # AS avg: (10.0 + -5.0) / 2 = 2.5
            assert result["adverse_selection_avg"] == 2.5

            # Should persist metrics
            mock_store.upsert_mm_daily_metrics.assert_called_once()
            persist_args = mock_store.upsert_mm_daily_metrics.call_args[0]
            assert persist_args[0] == today  # date
            assert persist_args[1] == result  # metrics dict

    @pytest.mark.asyncio
    async def test_as_avg_ignores_none_values(self, collector):
        """AS avg should skip fills where adverse_selection is None."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        fills = [
            _make_fill(fill_id=1, adverse_selection=20.0),
            _make_fill(fill_id=2, adverse_selection=None),  # Not yet measured
            _make_fill(fill_id=3, adverse_selection=10.0),
        ]
        for f in fills:
            f["created_at"] = f"{today}T12:00:00+00:00"

        with patch("mm.metrics_collector.store") as mock_store:
            mock_store.get_recent_mm_fills = AsyncMock(return_value=fills)
            mock_store.get_recent_mm_quotes = AsyncMock(return_value=[])
            mock_store.get_mm_inventory = AsyncMock(return_value=[])
            mock_store.upsert_mm_daily_metrics = AsyncMock()

            mock_db = MagicMock()
            mock_cursor = MagicMock()
            mock_cursor.fetchall = AsyncMock(return_value=[])
            mock_db.execute = AsyncMock(return_value=mock_cursor)
            mock_store._get_db = AsyncMock(return_value=mock_db)

            result = await collector.compute_daily_metrics()

            # Only fills 1 and 3 have AS: (20 + 10) / 2 = 15.0
            assert result["adverse_selection_avg"] == 15.0

    @pytest.mark.asyncio
    async def test_fill_quality_skips_zero_mid(self, collector):
        """fill_quality avg should skip fills where mid_at_fill is 0 or None."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        fills = [
            _make_fill(fill_id=1, side="BUY", price=0.48, mid_at_fill=0.50),
            _make_fill(fill_id=2, side="SELL", price=0.52, mid_at_fill=0),
            _make_fill(fill_id=3, side="BUY", price=0.49, mid_at_fill=None),
        ]
        for f in fills:
            f["created_at"] = f"{today}T12:00:00+00:00"

        with patch("mm.metrics_collector.store") as mock_store:
            mock_store.get_recent_mm_fills = AsyncMock(return_value=fills)
            mock_store.get_recent_mm_quotes = AsyncMock(return_value=[])
            mock_store.get_mm_inventory = AsyncMock(return_value=[])
            mock_store.upsert_mm_daily_metrics = AsyncMock()

            mock_db = MagicMock()
            mock_cursor = MagicMock()
            mock_cursor.fetchall = AsyncMock(return_value=[])
            mock_db.execute = AsyncMock(return_value=mock_cursor)
            mock_store._get_db = AsyncMock(return_value=mock_db)

            result = await collector.compute_daily_metrics()

            # Only fill_id=1 has valid mid_at_fill
            # fill_quality for BUY at 0.48 with mid 0.50 => (0.50 - 0.48) / 0.50 * 10000 = 400 bps
            assert result["fill_quality_avg"] == 400.0

    @pytest.mark.asyncio
    async def test_inventory_stats(self, collector):
        """max_inventory picks the largest absolute net_position across markets."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        fills = [_make_fill(fill_id=1)]
        fills[0]["created_at"] = f"{today}T12:00:00+00:00"

        inventory = [
            {"market_id": "m1", "net_position": "5.0"},
            {"market_id": "m2", "net_position": "-8.0"},
            {"market_id": "m3", "net_position": "3.0"},
        ]

        with patch("mm.metrics_collector.store") as mock_store:
            mock_store.get_recent_mm_fills = AsyncMock(return_value=fills)
            mock_store.get_recent_mm_quotes = AsyncMock(return_value=[])
            mock_store.get_mm_inventory = AsyncMock(return_value=inventory)
            mock_store.upsert_mm_daily_metrics = AsyncMock()

            mock_db = MagicMock()
            mock_cursor = MagicMock()
            mock_cursor.fetchall = AsyncMock(return_value=[])
            mock_db.execute = AsyncMock(return_value=mock_cursor)
            mock_store._get_db = AsyncMock(return_value=mock_db)

            result = await collector.compute_daily_metrics()

            # max(abs(5), abs(-8), abs(3)) = 8.0
            assert result["max_inventory"] == 8.0

    @pytest.mark.asyncio
    async def test_sharpe_with_historical_data(self, collector):
        """Rolling Sharpe uses historical daily metrics from DB."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        fills = [_make_fill(fill_id=1)]
        fills[0]["created_at"] = f"{today}T12:00:00+00:00"

        daily_returns = [
            {"pnl_net": 0.10, "portfolio_value": 100.0},
            {"pnl_net": -0.05, "portfolio_value": 100.0},
            {"pnl_net": 0.08, "portfolio_value": 100.0},
        ]

        with patch("mm.metrics_collector.store") as mock_store:
            mock_store.get_recent_mm_fills = AsyncMock(return_value=fills)
            mock_store.get_recent_mm_quotes = AsyncMock(return_value=[])
            mock_store.get_mm_inventory = AsyncMock(return_value=[])
            mock_store.upsert_mm_daily_metrics = AsyncMock()

            mock_db = MagicMock()
            mock_cursor = MagicMock()
            mock_cursor.fetchall = AsyncMock(return_value=daily_returns)
            mock_db.execute = AsyncMock(return_value=mock_cursor)
            mock_store._get_db = AsyncMock(return_value=mock_db)

            result = await collector.compute_daily_metrics()

            # With 3 daily returns, sharpe should be non-zero
            assert result["sharpe_7d"] != 0.0

    @pytest.mark.asyncio
    async def test_sharpe_zero_with_insufficient_data(self, collector):
        """< 2 daily returns => Sharpe = 0.0."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        fills = [_make_fill(fill_id=1)]
        fills[0]["created_at"] = f"{today}T12:00:00+00:00"

        with patch("mm.metrics_collector.store") as mock_store:
            mock_store.get_recent_mm_fills = AsyncMock(return_value=fills)
            mock_store.get_recent_mm_quotes = AsyncMock(return_value=[])
            mock_store.get_mm_inventory = AsyncMock(return_value=[])
            mock_store.upsert_mm_daily_metrics = AsyncMock()

            mock_db = MagicMock()
            mock_cursor = MagicMock()
            mock_cursor.fetchall = AsyncMock(return_value=[{"pnl_net": 0.10}])  # Only 1 row
            mock_db.execute = AsyncMock(return_value=mock_cursor)
            mock_store._get_db = AsyncMock(return_value=mock_db)

            result = await collector.compute_daily_metrics()

            assert result["sharpe_7d"] == 0.0

    @pytest.mark.asyncio
    async def test_profit_factor_infinity_clamped(self, collector):
        """All winning trades => profit_factor=inf clamped to 999.9."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        fills = [
            _make_fill(fill_id=1, side="BUY", price=0.40, size=10.0, mid_at_fill=0.50),
            _make_fill(fill_id=2, side="SELL", price=0.60, size=10.0, mid_at_fill=0.50),
        ]
        for f in fills:
            f["created_at"] = f"{today}T12:00:00+00:00"

        with patch("mm.metrics_collector.store") as mock_store:
            mock_store.get_recent_mm_fills = AsyncMock(return_value=fills)
            mock_store.get_recent_mm_quotes = AsyncMock(return_value=[])
            mock_store.get_mm_inventory = AsyncMock(return_value=[])
            mock_store.upsert_mm_daily_metrics = AsyncMock()

            mock_db = MagicMock()
            mock_cursor = MagicMock()
            mock_cursor.fetchall = AsyncMock(return_value=[])
            mock_db.execute = AsyncMock(return_value=mock_cursor)
            mock_store._get_db = AsyncMock(return_value=mock_db)

            result = await collector.compute_daily_metrics()

            # profit_factor for all winners = inf, clamped to 999.9
            assert result["profit_factor"] == 999.9

    @pytest.mark.asyncio
    async def test_upsert_failure_does_not_crash(self, collector):
        """Exception in upsert_mm_daily_metrics => metrics still returned."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        fills = [_make_fill(fill_id=1)]
        fills[0]["created_at"] = f"{today}T12:00:00+00:00"

        with patch("mm.metrics_collector.store") as mock_store:
            mock_store.get_recent_mm_fills = AsyncMock(return_value=fills)
            mock_store.get_recent_mm_quotes = AsyncMock(return_value=[])
            mock_store.get_mm_inventory = AsyncMock(return_value=[])
            mock_store.upsert_mm_daily_metrics = AsyncMock(
                side_effect=Exception("persist error")
            )

            mock_db = MagicMock()
            mock_cursor = MagicMock()
            mock_cursor.fetchall = AsyncMock(return_value=[])
            mock_db.execute = AsyncMock(return_value=mock_cursor)
            mock_store._get_db = AsyncMock(return_value=mock_db)

            result = await collector.compute_daily_metrics()

            # Should still return computed metrics despite persist failure
            assert result["fills_count"] == 1

    @pytest.mark.asyncio
    async def test_quotes_exception_gives_zero_scr(self, collector):
        """Exception in get_recent_mm_quotes => spread_capture_rate = 0.0."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        fills = [_make_fill(fill_id=1)]
        fills[0]["created_at"] = f"{today}T12:00:00+00:00"

        with patch("mm.metrics_collector.store") as mock_store:
            mock_store.get_recent_mm_fills = AsyncMock(return_value=fills)
            mock_store.get_recent_mm_quotes = AsyncMock(
                side_effect=Exception("quotes error")
            )
            mock_store.get_mm_inventory = AsyncMock(return_value=[])
            mock_store.upsert_mm_daily_metrics = AsyncMock()

            mock_db = MagicMock()
            mock_cursor = MagicMock()
            mock_cursor.fetchall = AsyncMock(return_value=[])
            mock_db.execute = AsyncMock(return_value=mock_cursor)
            mock_store._get_db = AsyncMock(return_value=mock_db)

            result = await collector.compute_daily_metrics()

            assert result["spread_capture_rate"] == 0.0

    @pytest.mark.asyncio
    async def test_inventory_exception_gives_zero_max(self, collector):
        """Exception in get_mm_inventory => max_inventory = 0."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        fills = [_make_fill(fill_id=1)]
        fills[0]["created_at"] = f"{today}T12:00:00+00:00"

        with patch("mm.metrics_collector.store") as mock_store:
            mock_store.get_recent_mm_fills = AsyncMock(return_value=fills)
            mock_store.get_recent_mm_quotes = AsyncMock(return_value=[])
            mock_store.get_mm_inventory = AsyncMock(
                side_effect=Exception("inventory error")
            )
            mock_store.upsert_mm_daily_metrics = AsyncMock()

            mock_db = MagicMock()
            mock_cursor = MagicMock()
            mock_cursor.fetchall = AsyncMock(return_value=[])
            mock_db.execute = AsyncMock(return_value=mock_cursor)
            mock_store._get_db = AsyncMock(return_value=mock_db)

            result = await collector.compute_daily_metrics()

            assert result["max_inventory"] == 0


# ────────────────────────────────────────────────────────────
# _hours_since_midnight
# ────────────────────────────────────────────────────────────

class TestHoursSinceMidnight:
    def test_returns_positive(self):
        """Should always return > 0 (floored at 0.1) and <= 24."""
        hours = MetricsCollector._hours_since_midnight()
        assert hours >= 0.1
        assert hours <= 24.0


# ────────────────────────────────────────────────────────────
# _get_token_id
# ────────────────────────────────────────────────────────────

class TestGetTokenId:
    """Tests for _get_token_id() caching and error handling."""

    @pytest.mark.asyncio
    async def test_returns_none_for_none_quote_id(self, collector):
        result = await collector._get_token_id(None)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_cached_value(self, collector):
        """Pre-populate cache => no DB call."""
        collector._token_cache[42] = "cached-token"
        result = await collector._get_token_id(42)
        assert result == "cached-token"

    @pytest.mark.asyncio
    async def test_returns_none_on_db_exception(self, collector):
        with patch("mm.metrics_collector.store") as mock_store:
            mock_store._get_db = AsyncMock(side_effect=Exception("db gone"))
            result = await collector._get_token_id(99)
            assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_no_row(self, collector):
        with patch("mm.metrics_collector.store") as mock_store:
            mock_db = MagicMock()
            mock_cursor = MagicMock()
            mock_cursor.fetchone = AsyncMock(return_value=None)
            mock_db.execute = AsyncMock(return_value=mock_cursor)
            mock_store._get_db = AsyncMock(return_value=mock_db)

            result = await collector._get_token_id(99)
            assert result is None


# ────────────────────────────────────────────────────────────
# _get_current_mid
# ────────────────────────────────────────────────────────────

class TestGetCurrentMid:
    """Tests for _get_current_mid() wrapper around client.get_book_summary."""

    @pytest.mark.asyncio
    async def test_returns_mid_from_book_summary(self, collector, mock_client):
        mid = await collector._get_current_mid("tok-1")
        assert mid == 0.55

    @pytest.mark.asyncio
    async def test_returns_none_when_summary_is_none(self, collector, mock_client):
        mock_client.get_book_summary.return_value = None
        mid = await collector._get_current_mid("tok-1")
        assert mid is None

    @pytest.mark.asyncio
    async def test_returns_none_on_exception(self, collector, mock_client):
        mock_client.get_book_summary.side_effect = Exception("network error")
        mid = await collector._get_current_mid("tok-1")
        assert mid is None
