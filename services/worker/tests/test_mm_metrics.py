"""Tests for mm.metrics module — Phase 5D additions."""

import sys
from pathlib import Path

import pytest

WORKER_DIR = Path(__file__).resolve().parents[1]
if str(WORKER_DIR) not in sys.path:
    sys.path.insert(0, str(WORKER_DIR))

from mm.metrics import profit_factor_from_round_trips


class TestProfitFactorFromRoundTrips:
    def test_mixed_pnl(self):
        rts = [
            {"net_pnl": 5.0},
            {"net_pnl": -2.0},
            {"net_pnl": 3.0},
            {"net_pnl": -1.0},
        ]
        pf = profit_factor_from_round_trips(rts)
        assert pf == pytest.approx(8.0 / 3.0)

    def test_all_gains(self):
        rts = [{"net_pnl": 5.0}, {"net_pnl": 3.0}]
        pf = profit_factor_from_round_trips(rts)
        assert pf == float("inf")

    def test_all_losses(self):
        rts = [{"net_pnl": -5.0}, {"net_pnl": -3.0}]
        pf = profit_factor_from_round_trips(rts)
        assert pf == 0.0

    def test_empty(self):
        assert profit_factor_from_round_trips([]) == 0.0
