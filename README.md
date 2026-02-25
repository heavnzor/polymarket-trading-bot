# Polymarket Trading Bot

**Autonomous quantitative trading on prediction markets**

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![TypeScript](https://img.shields.io/badge/TypeScript-5.x-blue.svg)](https://www.typescriptlang.org/)
[![Django 6](https://img.shields.io/badge/Django-6-green.svg)](https://www.djangoproject.com/)
[![Next.js 16](https://img.shields.io/badge/Next.js-16-black.svg)](https://nextjs.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A hybrid trading bot for [Polymarket](https://polymarket.com/) prediction markets, combining automated market-making with a directional crypto strategy. Powered by Claude AI for real-time market intelligence, risk assessment, and trade analysis.

---

## Highlights

- **Two complementary strategies** running concurrently: market-making (MM) on wide spreads + crypto-directional (CD) via Student-t model
- **6 concurrent async loops** orchestrated via asyncio — from 5-second quote cycles to 6-hour deep analysis
- **Claude AI at 5 levels** — guard (Opus), market scoring (Sonnet), NL parsing (Sonnet), exit confirmation (Haiku), post-trade analysis (Opus)
- **Avellaneda-Stoikov pricing engine** with dynamic gamma, kappa estimation, and adverse-selection feedback loop
- **Full risk management** — drawdown kill switch with auto-recovery, circuit breakers, Kelly sizing, exposure limits
- **CTF operations** — split/merge positions via Polymarket's Conditional Token Framework contracts
- **Complete-set arbitrage** — automated buy-merge and split-sell when YES+NO misprice
- **Real-time dashboard** — Django + Next.js control plane with WebSocket updates
- **Telegram notifications** — alerts for trades, kills, recoveries, and critical errors

---

## Architecture

```
                         +------------------+
                         |   Next.js 16     |
                         |   Dashboard UI   |
                         +--------+---------+
                                  |
                           WebSocket + REST
                                  |
                         +--------+---------+
                         |   Django 6 + DRF |
                         |   Control Plane  |
                         +--------+---------+
                                  |
                            Bridge (sync)
                                  |
+-----------------------------+---+---+-----------------------------+
|                             |       |                             |
|  MM Loop (5-10s)     CD Loop (15m)  |  Guard (5m)                |
|  - Scan markets      - NL parse     |  - Resolution traps        |
|  - Score (Sonnet)    - Student-t    |  - Kill markets (Opus)     |
|  - AS pricing        - Kelly size   |                            |
|  - Quote bid/ask     - Trade        |  CD Exit (2m)              |
|  - Detect fills                     |  - Stop-loss / TP          |
|  - Inventory mgmt    CD Analysis    |  - Edge reversal (Haiku)   |
|  - Arbitrage         (6h, Opus)     |                            |
|                                     |  Maintenance (30s)         |
|                                     |  - Reconciliation          |
|                                     |  - Metrics pipeline        |
+-------------------------------------+----------------------------+
                             |
                     +-------+--------+
                     |  Polymarket    |
                     |  CLOB + CTF    |
                     |  (Polygon)     |
                     +----------------+
```

---

## Strategies

### Market Making (MM)

Continuously quotes bid/ask on prediction markets with competitive spreads.

- **Avellaneda-Stoikov** pricing: reservation price adjusted for inventory risk, optimal spread via order arrival intensity
- **Legacy pricing** fallback: VWAP mid, dynamic delta, EWMA volatility, non-linear inventory skew
- **Multi-level quotes**: tight/medium/wide levels with configurable spread multipliers
- **Two-sided**: maintains both YES and NO inventory via CTF split/merge
- **Adverse selection measurement**: T+30s and T+120s mid-price tracking with feedback into gamma
- **Complete-set arbitrage**: exploits YES+NO mispricings via merge/split (gas-only cost)
- **Circuit breaker**: per-market suspension after consecutive adverse fills

### Crypto Directional (CD)

Takes directional positions on crypto-correlated markets using quantitative signals.

- **Student-t(nu=6)** model on BTC/ETH price data
- **EWMA volatility** estimation
- **Kelly criterion** position sizing (fractional, capped)
- **NL parsing** via Claude Sonnet for market question understanding
- **Edge decay**: positions degrade as expiry approaches
- **Automated exits**: stop-loss, take-profit, edge reversal with AI confirmation

---

## AI Integration

| Usage | Model | Frequency | Purpose |
|-------|-------|-----------|---------|
| Guard | Opus | 1 call/5min | Detect resolution traps, imminent catalysts, kill dangerous markets |
| MM Scorer | Sonnet | 1 call/refresh | Score market quality: resolution clarity, liquidity, profitability |
| NL Parser | Sonnet | 1 call/15min | Parse market questions into structured crypto signals |
| Exit Confirm | Haiku | On edge reversal | Confirm exit decisions on directional positions |
| Post-trade Analysis | Opus | 4 calls/day | Review trade quality, suggest parameter adjustments |
| Pre-trade Validation | Haiku | Per CD trade | Portfolio context, volatility regime, spot/strike distance |

---

## Risk Management

- **Drawdown kill switch** with auto-recovery (hysteresis + cooldown + daily limit)
- **Per-market circuit breakers** after consecutive adverse fills
- **Global exposure cap** (% of portfolio)
- **Kelly fraction cap** for position sizing
- **Pre-flight balance checks** — BUY verifies USDC balance, SELL verifies token balance
- **Quote validation** — spread limits, crossing protection, stale market detection
- **Inventory reduce mode** — restrict to reduce-only quotes at extreme positions

---

## Tech Stack

| Component | Technology |
|-----------|------------|
| Worker | Python 3.11+, asyncio, scipy, py-clob-client, aiosqlite |
| Control Plane | Django 6, DRF, Channels, Celery, PostgreSQL, Redis |
| Frontend | Next.js 16, React 19, TypeScript, TanStack Query |
| AI | Claude (Opus / Sonnet / Haiku) via Anthropic API |
| Blockchain | Polygon, USDC.e, CTF ERC-1155 |
| Deployment | Docker Compose, PM2, nginx |
| Notifications | Telegram Bot API |

---

## Quick Start

### 1. Clone & setup

```bash
git clone https://github.com/YOUR_USERNAME/polymarket-trading-bot.git
cd polymarket-trading-bot
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env with your credentials:
#   - POLYMARKET_PRIVATE_KEY (Polygon wallet)
#   - POLYMARKET_FUNDER_ADDRESS (proxy wallet)
#   - ANTHROPIC_API_KEY
#   - TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID
```

### 3. Install worker dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -r services/worker/requirements.txt
```

### 4. Start the control plane

```bash
docker compose -f docker-compose.control-plane.yml up -d --build
```

### 5. Start the worker

```bash
# Via PM2 (recommended for production)
pm2 start ecosystem.config.js

# Or directly
python services/worker/run_worker.py
```

---

## Project Structure

```
apps/
  backend/              Django control plane (API, WebSocket, Celery)
  frontend/             Next.js dashboard UI

services/worker/
  main.py               Orchestrator — 6 concurrent loops
  config.py             Configuration dataclasses
  ai/                   Claude multi-model caller
  mm/                   Market-making module
    as_engine.py          Avellaneda-Stoikov pricing
    engine.py             Legacy pricing engine
    loop.py               Main MM loop
    proposal.py           Quote proposal pipeline
    scanner.py            Market discovery
    scorer.py             AI market scoring
    quoter.py             Order lifecycle
    inventory.py          Position tracking
    arbitrage.py          Complete-set arbitrage
    metrics.py            Analytics & Sharpe
    metrics_collector.py  AS measurement pipeline
  strategy/             Crypto directional module
    cd_loop.py            Signal generation
    cd_exit.py            Automated exits
    cd_analysis.py        Post-trade AI review
    crypto_directional.py Student-t model
  executor/client.py    CLOB wrapper + CTF operations
  monitor/              Risk, portfolio, performance
  data/                 Market data, orderbook
  db/store.py           SQLite WAL storage
  tests/                Pytest suite

scripts/                Deploy & utility scripts
docs/                   Architecture & operations docs
```

---

## Configuration

All configuration is via environment variables (see [`.env.example`](.env.example)).

Key parameters:

| Variable | Default | Description |
|----------|---------|-------------|
| `MM_ENABLED` | `true` | Enable market-making strategy |
| `CD_ENABLED` | `true` | Enable crypto-directional strategy |
| `MM_PRICING_ENGINE` | `legacy` | Pricing engine: `legacy` or `as` (Avellaneda-Stoikov) |
| `MM_MAX_MARKETS` | `8` | Max concurrent MM markets |
| `MM_QUOTE_SIZE_USD` | `6` | Base quote size in USD |
| `MM_TWO_SIDED` | `true` | Quote both YES and NO sides |
| `MM_ARB_ENABLED` | `false` | Enable complete-set arbitrage |
| `CD_KELLY_FRACTION` | `0.20` | Kelly fraction for CD sizing |
| `CD_MAX_POSITION_PCT` | `8` | Max CD position as % of portfolio |
| `GUARD_ENABLED` | `true` | Enable Claude guard loop |
| `MM_DD_KILL_PCT` | configurable | Drawdown % to trigger kill switch |

See `.env.example` for the full list.

---

## Documentation

Detailed documentation is available in [`docs/`](docs/):

- **Architecture** — system design, data flows, component interactions
- **API** — REST endpoints, WebSocket protocol, bridge sync
- **Operations** — VPS deployment, monitoring, troubleshooting

---

## Disclaimer

This software trades with **real money** on Polymarket prediction markets. Use at your own risk. The authors are not responsible for any financial losses incurred through the use of this software.

- No paper trading mode — all trades are executed on-chain
- Prediction markets carry significant risk of total loss
- Past performance does not guarantee future results
- This is experimental software provided as-is

---

## License

[MIT](LICENSE)
