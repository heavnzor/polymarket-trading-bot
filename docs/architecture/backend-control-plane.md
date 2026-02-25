# Backend Control Plane

Location: `apps/backend`

## Responsibilities

- Expose REST APIs for dashboard and bridge.
- Serve websocket channels for realtime UI updates.
- Persist operational state in PostgreSQL.
- Queue async tasks through Celery.

## MM/CD Models

Dedicated hybrid-trading models:

- `MMQuote`: active bid/ask quote pairs (market, prices, size, status).
- `MMInventory`: per-market net position, avg entry, realized/unrealized PnL.
- `MMDailyMetric`: daily aggregates (fills, round trips, spread capture, PnL, Sharpe).
- `CDSignal`: edge detection signals (coin, strike, spot, vol, p_model, p_market, edge, action).

## Learning/Risk Models

Active modules synced from worker via bridge:

- `RiskOfficerReview`: pre-execution risk assessments from Claude risk agent.
- `StrategistAssessment`: portfolio strategy recommendations from Claude strategist agent.

## Legacy Models (retained)

Core models remain for historical data and dashboard compatibility:

- `Trade`, `Position`, `PerformanceSnapshot`, `OrderEvent`
- `BotStatus`, `BotSetting`, `BotCommand`
- `LearningJournal`, `LearningInsight`, `LearningProposal`
- `ChatMessage`, `FileChangeAudit`

## MM/CD API Viewsets

- `MMQuoteViewSet` (read-only)
- `MMInventoryViewSet` (read-only)
- `MMDailyMetricViewSet` (read-only)
- `CDSignalViewSet` (read-only)

Routes are registered under `/api/v1/`.
