# Worker Service

Location: `services/worker`

## Trading Mode

- The worker runs in **real-money mode only**.
- Paper trading toggles/branches were removed from runtime code.

## Core Modules

- `main.py`: orchestrates six concurrent loops (MM, CD, CD Exit, CD Analysis, Guard, Maintenance). Heartbeat loop uses `max_failures_before_reconnect=20` with exponential backoff between reconnects (30s, 60s, 120s max).
- `config.py`: dataclasses config (`MarketMakingConfig`, `CryptoDirectionalConfig`, `ClaudeGuardConfig`, `TradingConfig`, `AnthropicConfig` with multi-model support).
- `ai/claude_caller.py`: shared Claude API caller with multi-model support (`ModelTier.OPUS/SONNET/HAIKU`), `call_claude()`, `call_claude_json()`.
- `mm/`: market-making core (`scanner`, `scorer`, `engine`, `as_engine`, `proposal`, `quoter`, `inventory`, `metrics`, `metrics_collector`, `state`, `loop`, `claude_guard`, `arbitrage`). **Two-sided quoting**: the scanner extracts both YES and NO token IDs + `condition_id` per market. The loop can split USDC.e into YES+NO tokens via the CTF contract to seed inventory for selling, and periodically merges YES+NO pairs back to USDC.e to free capital (`mm_use_split_merge`, `mm_merge_threshold`). Inventory tracks YES and NO positions separately with independent P&L. Anti-churn: quotes younger than `mm_min_quote_lifetime_seconds` are not requoted. Optional Sonnet-based market scorer (`mm/scorer.py`) evaluates candidates on resolution clarity, market quality, and profitability before quoting (`MM_SCORER_ENABLED`). Inventory reconciliation with DB every ~10 min + phantom order detection. `mm/metrics_collector.py`: adverse selection measurement pipeline (T+30s, T+120s mid tracking after each fill) + daily metrics aggregation (fill quality, spread capture, profit factor, Sharpe 7d, portfolio value, inventory turns); called from the maintenance loop. `mm/engine.py` includes `VolTracker` class for EWMA volatility tracking; `compute_skew()` uses non-linear (quadratic) skew for extreme inventory levels; `compute_dynamic_delta()` accepts `tracked_vol` from VolTracker. `mm/inventory.py` tracks `opened_at` for position aging; `get_unwind_urgency()` provides a time-based urgency factor for skew amplification; supports reduce-only mode when inventory exceeds thresholds. `mm/arbitrage.py`: complete-set arbitrage module — see dedicated section below. `mm/as_engine.py`: Avellaneda-Stoikov pricing engine (reservation price, optimal spread, dynamic gamma, `KappaEstimator`); activated via `MM_PRICING_ENGINE=as`. `mm/proposal.py`: composable quote proposal pipeline (multi-level, vol widen, event risk, budget constraint, hanging orders); integrates with `mm/quoter.py`.
- `strategy/crypto_directional.py`: Student-t modeling, edge detection, NL market parsing via Sonnet (`parse_markets_batch()`).
- `strategy/cd_loop.py`: periodic directional signal loop (entry), uses NL parsing when `cd_nl_parsing_enabled`. Enforces position count limit (`cd_max_concurrent_positions`), risk validation, global exposure check, and pre-trade AI validation (Haiku, enabled by default) when `cd_pretrade_ai_enabled`. The pre-trade validation prompt is enriched with portfolio context (open positions, exposure %, coin overlap), volatility regime classification (low/normal/high based on EWMA daily vol thresholds), and spot-vs-strike distance. AI validation responses are stored in the `ai_validation` column of `cd_signals` for post-hoc analysis.
- `strategy/cd_exit.py`: automatic exit monitoring loop (stop-loss, take-profit, edge reversal with AI confirmation via Haiku). Edge recalculation uses the actual CLOB midpoint as `p_market` (instead of a fixed 0.5 baseline) and the position's remaining expiry days (tracked in `cd_positions`, degraded by time elapsed since position opened) instead of a hardcoded 30 days.
- `strategy/cd_analysis.py`: post-trade analysis loop (6h), Claude Opus review of CD trade quality. Optional auto-apply of parameter suggestions with safety bounds when `cd_analysis_auto_apply` is enabled.
- `executor/client.py`: CLOB wrapper (order placement with pre-flight BUY + SELL balance checks, cancel, `get_book_summary()`). Includes CTF contract operations: `split_position()`, `merge_positions()`, `get_token_balance()` via raw RPC + `eth_abi` encoding. Supports `known_balance` parameter on `place_limit_order()` to avoid redundant RPC calls in the MM loop.
- `executor/trader.py`: order execution, fill tracking.
- `monitor/risk.py`: MM quote validation (including `mm_max_spread_pts` and `mm_stoploss_max_spread_pts` checks), stop-loss, drawdown, global exposure check, per-market circuit breaker (suspends a market after N consecutive adverse fills for a configurable cooldown), auto-recovery (hysteresis + cooldown). RiskManager is passed to MM, CD, and CD Exit loops and actively enforces pause gates, quote validation, CD trade validation, and exposure limits. After a kill switch trigger, `try_auto_resume()` can automatically resume trading when DD recovers below `mm_dd_resume_pct` after a cooldown period.
- `monitor/portfolio.py`: positions, P&L, reconciliation.
- `monitor/performance.py`: resolution, calibration, bias.
- `conversation/router.py`: interface conversationnelle NL Claude-first (Telegram + dashboard), confirmations d'actions sensibles (`kill`, `restart`, `update_settings`), diagnostic logs et persistence en DB.
- `data/markets.py`: Gamma API, categories, history.
- `data/orderbook.py`: CLOB order book.
- `notifications/telegram_bot.py`: send-only Telegram notifications (trade alerts, risk alerts, daily summaries). No polling — all user interaction goes through `claude-telegram` (Claude Agent SDK).
- `db/store.py`: SQLite store with legacy + MM + CD + learning tables.
- `bridge.py`: worker state sync to control-plane backend (trades, positions, MM, CD, learning, risk).

## Learning Modules (active)

These modules are NOT part of the 6 main loops but are called on-demand and synced to the control-plane via `bridge.py`:

- `learning/strategist.py`: Claude agent for portfolio analysis, regime detection, strategic recommendations.
- `learning/risk_officer.py`: Claude agent for pre-execution risk review.
- `learning/managed_autocorrect_rules.json`: managed autocorrection rules.

Data synced to backend models `StrategistAssessment` and `RiskOfficerReview`.

## Removed Legacy Modules

The legacy 9-agent pipeline and old heavy modules were removed:

- `agent/analyst.py` (9-agent pipeline)
- `strategy/active.py`, `strategy/base.py`
- `data/news.py`, `data/fusion.py`
- `learning/journal.py`, `learning/insights.py`, `learning/proposals.py`, `learning/autocorrect.py`, `learning/shadow.py`, `learning/manager.py`, `learning/developer.py`

## Avellaneda-Stoikov Pricing Engine (mm/as_engine.py)

Implements the Avellaneda-Stoikov (2008) high-frequency market-making model as a pure math layer (no I/O).

**Core equations:**
- `reservation_price = mid - q * gamma * sigma^2 * T` — inventory-adjusted indifference price. A long position (q > 0) lowers the reservation price, incentivizing sell-side quotes.
- `optimal_spread = gamma * sigma^2 * T + (2/gamma) * ln(1 + gamma/kappa)` — spread derived from risk aversion and order arrival intensity.
- `dynamic_gamma = gamma_base * (1 + alpha * |q/q_max|)` — risk aversion increases with inventory concentration.

**`KappaEstimator`** tracks empirical fill frequency on a rolling window (`mm_as_kappa_window_minutes`) to calibrate `kappa` from real order arrival data rather than a fixed default.

**Activation**: set `MM_PRICING_ENGINE=as` (default is `legacy`, which uses `mm/engine.py`).

**Configuration** (`.env`):

| Variable | Default | Description |
|----------|---------|-------------|
| `MM_PRICING_ENGINE` | `legacy` | Pricing engine to use: `as` or `legacy` |
| `MM_AS_GAMMA_BASE` | `0.1` | Base risk aversion parameter |
| `MM_AS_GAMMA_ALPHA` | `0.5` | Inventory scaling factor for dynamic gamma |
| `MM_AS_KAPPA_DEFAULT` | `1.5` | Default order arrival intensity (no data) |
| `MM_AS_KAPPA_WINDOW_MINUTES` | `60` | Rolling window for kappa estimation |

## Quote Proposal Pipeline (mm/proposal.py)

Composable pipeline that transforms a base `QuoteProposal` through successive stages before sending to `quoter.py`. Each stage is a pure function that returns a modified proposal.

**Pipeline stages:**
1. `create_base_proposal` — single level-0 bid + ask from engine prices.
2. `apply_multi_level` — adds deeper levels (medium/wide) at multiplied spreads and sizes.
3. `apply_vol_widen` — widens all levels when realised vol exceeds `mm_vol_widen_threshold`.
4. `apply_event_risk` — applies an additional percentage widen (`mm_event_risk_widen_pct`) when a guard event risk flag is active.
5. `apply_budget_constraint` — caps sizes to available capital budget.

**Hanging orders**: quotes that remain live beyond the anti-churn lifetime may be designated as hanging orders in `quoter.py`, surviving requote cycles to avoid unnecessary cancels.

**Configuration** (`.env`):

| Variable | Default | Description |
|----------|---------|-------------|
| `MM_MULTI_LEVEL_COUNT` | `1` | Number of quote levels (1 = tight only) |
| `MM_LEVEL_SPREAD_MULT` | `1.5` | Spread multiplier per additional level |
| `MM_LEVEL_SIZE_MULT` | `2.0` | Size multiplier per additional level |
| `MM_HANGING_ORDERS` | `true` | Enable hanging orders in quoter |
| `MM_VOL_WIDEN_THRESHOLD` | `5.0` | Vol threshold (pts) to trigger widen |
| `MM_EVENT_RISK_WIDEN_PCT` | `50.0` | Extra spread widen % under event risk |

## Per-Market Circuit Breaker

The `RiskManager` in `monitor/risk.py` includes a per-market circuit breaker independent of the global drawdown kill switch.

**Mechanism**: after `mm_circuit_breaker_threshold` consecutive adverse fills on a single market, that market is suspended for `mm_circuit_breaker_cooldown` seconds. Once the cooldown expires, quoting resumes automatically. Suspension is tracked in memory and does not affect other markets.

**Configuration** (`.env`):

| Variable | Default | Description |
|----------|---------|-------------|
| `MM_CIRCUIT_BREAKER_THRESHOLD` | `5` | Consecutive adverse fills to trigger suspension |
| `MM_CIRCUIT_BREAKER_COOLDOWN` | `300` | Suspension duration in seconds |

## AS Feedback Loop

When `mm_as_feedback_enabled=true`, the MM loop reads the rolling average adverse selection (bps measured at T+120s by `metrics_collector.py`) and adjusts the AS engine `gamma` parameter accordingly:

- If average AS > `mm_as_feedback_threshold_bps`: `gamma` is increased, widening quotes to compensate for adverse selection.
- If average AS < threshold: `gamma` is slightly decreased, tightening quotes to capture more spread.

**Configuration** (`.env`):

| Variable | Default | Description |
|----------|---------|-------------|
| `MM_AS_FEEDBACK_ENABLED` | `true` | Enable AS-to-gamma feedback loop |
| `MM_AS_FEEDBACK_THRESHOLD_BPS` | `50.0` | AS threshold in bps for gamma adjustment |

## Complete-Set Arbitrage (mm/arbitrage.py)

The MM module includes a **complete-set arbitrage** scanner that exploits mispricings between YES and NO tokens on the same market. On Polymarket, YES + NO tokens for a binary market always resolve to exactly $1.00. Merge and split operations go through the CTF contract which is fee-free (no Polymarket fees). Gas cost on Polygon is ~$0.005 per transaction.

Two strategies are implemented:

- **Buy-merge**: when `best_ask(YES) + best_ask(NO) < $1.00`, the bot buys both YES and NO tokens and merges them into USDC.e via the CTF contract. Profit = `$1.00 - cost(YES) - cost(NO) - gas`.
- **Split-sell**: when `best_bid(YES) + best_bid(NO) > $1.00`, the bot splits USDC.e into YES+NO token pairs via the CTF contract and sells both on the orderbook. Profit = `revenue(YES) + revenue(NO) - $1.00 - gas`.

Minimum trade size: 5 shares. Arb fills are tracked in the `mm_fills` table with `side="ARB"`.

**Integration**: the arbitrage scan runs as step 5c inside the MM loop, every 3 cycles (~30 seconds). It is gated by `mm_arb_enabled` (default `false`).

**Configuration** (`.env`):

| Variable | Default | Description |
|----------|---------|-------------|
| `MM_ARB_ENABLED` | `false` | Enable/disable the arbitrage scanner |
| `MM_ARB_MIN_PROFIT_PCT` | `0.5` | Minimum profit threshold in % to execute an arb |
| `MM_ARB_MAX_SIZE_USD` | `50.0` | Maximum size per arb trade in USD |
| `MM_ARB_GAS_COST_USD` | `0.005` | Estimated gas cost per transaction on Polygon |

## Loop Cadence

| Loop | Config key | Default | AI Model |
|------|-----------|---------|----------|
| MM | `mm_cycle_seconds` | 10s | Sonnet (scorer, opt-in) |
| CD | `cd_cycle_minutes` | 15 min | Sonnet (NL parsing) |
| CD Exit | `cd_exit_check_seconds` | 120s | Haiku (edge reversal confirm) |
| CD Analysis | `cd_analysis_interval_hours` | 6h | Opus (post-trade review) |
| Claude Guard | `guard_interval_minutes` | 5 min | Opus |
| Maintenance | hardcoded | 30s | — (adverse selection measurement every cycle, daily metrics aggregation every ~10 min) |

## Phase 5 New Configuration Parameters

The following parameters were added in Phase 5 (MM refactoring). All are set via `.env`.

**Phase 5A — Bug fixes and scanner:**

| Variable | Default | Description |
|----------|---------|-------------|
| `MM_MAX_SPREAD_PTS` | `12.0` | Maximum quote spread in points; wider quotes rejected by RiskManager |
| `MM_SCANNER_CONCURRENCY` | `10` | Max concurrent Gamma API fetches in scanner |
| `MM_STALE_THRESHOLD_SECONDS` | `60.0` | Seconds of inactivity before StaleTracker removes a market |
| `MM_STOPLOSS_MAX_SPREAD_PTS` | `8.0` | Max spread threshold for stop-loss quote validation |

**Phase 5B — Avellaneda-Stoikov engine:**

| Variable | Default | Description |
|----------|---------|-------------|
| `MM_PRICING_ENGINE` | `legacy` | Pricing engine: `as` (Avellaneda-Stoikov) or `legacy` |
| `MM_AS_GAMMA_BASE` | `0.1` | Base risk aversion |
| `MM_AS_GAMMA_ALPHA` | `0.5` | Inventory scaling for dynamic gamma |
| `MM_AS_KAPPA_DEFAULT` | `1.5` | Default order arrival intensity |
| `MM_AS_KAPPA_WINDOW_MINUTES` | `60` | Rolling window for kappa estimation from fills |

**Phase 5C — Proposal pipeline:**

| Variable | Default | Description |
|----------|---------|-------------|
| `MM_MULTI_LEVEL_COUNT` | `1` | Quote levels per side (1 = single tight level) |
| `MM_LEVEL_SPREAD_MULT` | `1.5` | Spread multiplier per deeper level |
| `MM_LEVEL_SIZE_MULT` | `2.0` | Size multiplier per deeper level |
| `MM_HANGING_ORDERS` | `true` | Preserve live quotes across requote cycles |
| `MM_VOL_WIDEN_THRESHOLD` | `5.0` | Vol (pts) to activate vol-widen stage |
| `MM_EVENT_RISK_WIDEN_PCT` | `50.0` | Additional spread widen % under event risk |

**Phase 5D — Risk and feedback:**

| Variable | Default | Description |
|----------|---------|-------------|
| `MM_CIRCUIT_BREAKER_THRESHOLD` | `5` | Consecutive adverse fills to suspend a market |
| `MM_CIRCUIT_BREAKER_COOLDOWN` | `300` | Market suspension duration in seconds |
| `MM_AS_FEEDBACK_ENABLED` | `true` | Enable AS-to-gamma feedback loop |
| `MM_AS_FEEDBACK_THRESHOLD_BPS` | `50.0` | AS bps threshold for gamma adjustment |
| `MM_CD_SYNERGY_ENABLED` | `false` | Adjust MM quotes based on CD directional signal |
| `MM_CD_SYNERGY_WEIGHT` | `0.3` | Weight of CD signal in MM quote skew |

## DB Schema Changes (Phase 5)

`mm_daily_metrics` table extended with three new columns:

| Column | Type | Description |
|--------|------|-------------|
| `profit_factor` | REAL | Gross profit / gross loss ratio from round trips |
| `sharpe_7d` | REAL | Rolling 7-day Sharpe ratio |
| `portfolio_value` | REAL | Total portfolio value at snapshot time |

A migration must be applied on the VPS when deploying Phase 5.
