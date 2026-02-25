import os
from pathlib import Path
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()
PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass
class PolymarketConfig:
    host: str = "https://clob.polymarket.com"
    gamma_api: str = "https://gamma-api.polymarket.com"
    chain_id: int = 137
    private_key: str = ""
    funder_address: str = ""
    signature_type: int = 1  # POLY_GNOSIS_SAFE for Polymarket web accounts
    rpc_url: str = "https://polygon-rpc.com"
    usdc_e_address: str = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
    ctf_address: str = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
    ctf_exchange_address: str = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8b8982E"
    neg_risk_ctf_exchange_address: str = "0xC5d563A36AE78145C45a50134d48A1215220f80a"
    neg_risk_adapter_address: str = "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"

    def __post_init__(self):
        self.private_key = os.getenv("POLYMARKET_PRIVATE_KEY", "")
        self.funder_address = os.getenv("POLYMARKET_FUNDER_ADDRESS", "")
        self.rpc_url = os.getenv("POLYGON_RPC_URL", "https://polygon-rpc.com")


@dataclass
class AnthropicConfig:
    api_key: str = ""
    base_url: str = ""
    model: str = "claude-opus-4-6"
    model_sonnet: str = "claude-sonnet-4-6"
    model_haiku: str = "claude-haiku-4-5"
    api_version: str = "2024-05-01-preview"

    def __post_init__(self):
        self.api_key = os.getenv("ANTHROPIC_API_KEY", "")
        self.base_url = os.getenv("ANTHROPIC_FOUNDRY_BASE_URL", "")
        self.model = os.getenv("ANTHROPIC_MODEL", "claude-opus-4-6")
        self.model_sonnet = os.getenv("ANTHROPIC_MODEL_SONNET", "claude-sonnet-4-6")
        self.model_haiku = os.getenv("ANTHROPIC_MODEL_HAIKU", "claude-haiku-4-5")
        self.api_version = os.getenv("ANTHROPIC_API_VERSION", "2024-05-01-preview")


@dataclass
class TelegramConfig:
    bot_token: str = ""
    chat_id: str = ""

    def __post_init__(self):
        self.bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID", "")


@dataclass
class TradingConfig:
    """Shared trading parameters.

    Capital source of truth: on-chain USDC.e balance via Polymarket API.
    No hardcoded budget — the bot uses whatever is available on-chain.
    """
    stop_loss_percent: float = 20.0
    drawdown_stop_loss_percent: float = 25.0
    heartbeat_enabled: bool = True
    heartbeat_interval_seconds: int = 5
    risk_officer_enabled: bool = True
    strategist_enabled: bool = True
    conversation_enabled: bool = True
    conversation_max_history: int = 20
    max_total_exposure_pct: float = 75.0

    def __post_init__(self):
        self.stop_loss_percent = float(os.getenv("STOP_LOSS_PERCENT", 20))
        self.drawdown_stop_loss_percent = float(os.getenv("DRAWDOWN_STOP_LOSS_PERCENT", 25))
        self.heartbeat_enabled = os.getenv("HEARTBEAT_ENABLED", "true").lower() in ("true", "1", "yes")
        self.heartbeat_interval_seconds = int(os.getenv("HEARTBEAT_INTERVAL_SECONDS", 5))
        self.risk_officer_enabled = os.getenv("RISK_OFFICER_ENABLED", "true").lower() in ("true", "1", "yes")
        self.strategist_enabled = os.getenv("STRATEGIST_ENABLED", "true").lower() in ("true", "1", "yes")
        self.conversation_enabled = os.getenv("CONVERSATION_ENABLED", "true").lower() in ("true", "1", "yes")
        self.conversation_max_history = int(os.getenv("CONVERSATION_MAX_HISTORY", 20))
        self.max_total_exposure_pct = float(os.getenv("MAX_TOTAL_EXPOSURE_PCT", 75.0))


@dataclass
class MarketMakingConfig:
    """Market-making strategy parameters.

    No hardcoded exposure/inventory caps — the bot uses the full on-chain
    balance via the Polymarket API. Per-market inventory is derived
    dynamically as available_balance / mm_max_markets.
    """
    mm_enabled: bool = True
    mm_cycle_seconds: int = 10
    # Spread & delta
    mm_min_spread_pts: float = 3.0
    mm_delta_min: float = 1.5
    mm_delta_max: float = 8.0
    mm_quote_size_usd: float = 5.0
    # Inventory
    mm_inventory_skew_factor: float = 0.5
    mm_unwind_threshold: float = 0.8
    # Quote lifecycle
    mm_stale_quote_seconds: int = 30
    mm_requote_threshold: float = 0.5
    mm_post_only: bool = True
    mm_cross_reject_threshold: int = 3
    mm_cross_cooldown_seconds: int = 300
    mm_cross_cooldown_max_seconds: int = 600
    # Market filters
    mm_min_depth_usd: float = 500.0
    mm_min_activity_per_min: float = 1.0
    mm_max_markets: int = 10
    mm_scanner_refresh_minutes: int = 5
    # Kill switch (drawdown % from HWM — percentage-based)
    mm_dd_reduce_pct: float = 15.0
    mm_dd_kill_pct: float = 25.0
    # Auto-recovery (hysteresis)
    mm_dd_resume_pct: float = 20.0
    mm_dd_cooldown_minutes: float = 30.0
    mm_dd_max_recoveries_per_day: int = 3
    # Metrics
    mm_adverse_selection_window: int = 120
    # Early market detection
    mm_early_market_hours: int = 48
    mm_early_market_min_depth_usd: float = 100.0
    mm_early_market_min_activity: float = 0.1
    mm_early_market_boost: int = 3
    mm_early_market_max_slots: int = 3
    # Two-sided quoting with split/merge
    mm_two_sided: bool = True
    mm_use_split_merge: bool = True
    mm_split_size_usd: float = 5.0
    mm_merge_threshold: float = 10.0
    mm_min_quote_lifetime_seconds: int = 10
    mm_cancel_price_threshold: float = 1.5
    mm_cancel_size_threshold: float = 25.0
    # Complete-set arbitrage
    mm_arb_enabled: bool = False
    mm_arb_min_profit_pct: float = 0.5
    mm_arb_max_size_usd: float = 50.0
    mm_arb_gas_cost_usd: float = 0.005
    # Scorer AI (Sonnet) — qualitative market evaluation before quoting
    mm_scorer_enabled: bool = False
    mm_scorer_min_score: float = 5.0
    mm_scorer_cache_minutes: int = 10
    # Phase 5A: max spread cap, scanner concurrency, stale tracker
    mm_max_spread_pts: float = 12.0
    mm_scanner_concurrency: int = 10
    mm_stale_threshold_seconds: float = 60.0
    # Phase 5B: Avellaneda-Stoikov pricing engine
    mm_pricing_engine: str = "as"  # "as" or "legacy"
    mm_as_gamma_base: float = 0.1
    mm_as_gamma_alpha: float = 0.5
    mm_as_kappa_default: float = 1.5
    mm_as_kappa_window_minutes: int = 60
    # Phase 5C: Quote management
    mm_multi_level_count: int = 1
    mm_level_spread_mult: float = 1.5
    mm_level_size_mult: float = 2.0
    mm_hanging_orders: bool = True
    mm_vol_widen_threshold: float = 5.0
    mm_event_risk_widen_pct: float = 50.0
    # Phase 5D: Risk & feedback
    mm_circuit_breaker_threshold: int = 5
    mm_circuit_breaker_cooldown: int = 300
    mm_as_feedback_enabled: bool = True
    mm_as_feedback_threshold_bps: float = 50.0
    mm_cd_synergy_enabled: bool = False
    mm_cd_synergy_weight: float = 0.3
    mm_stoploss_max_spread_pts: float = 8.0

    def __post_init__(self):
        self.mm_enabled = os.getenv("MM_ENABLED", "true").lower() in ("true", "1", "yes")
        self.mm_cycle_seconds = int(os.getenv("MM_CYCLE_SECONDS", 10))
        self.mm_min_spread_pts = float(os.getenv("MM_MIN_SPREAD_PTS", 3.0))
        self.mm_delta_min = float(os.getenv("MM_DELTA_MIN", 1.5))
        self.mm_delta_max = float(os.getenv("MM_DELTA_MAX", 8.0))
        self.mm_quote_size_usd = float(os.getenv("MM_QUOTE_SIZE_USD", 5.0))
        self.mm_inventory_skew_factor = float(os.getenv("MM_INVENTORY_SKEW_FACTOR", 0.5))
        self.mm_unwind_threshold = float(os.getenv("MM_UNWIND_THRESHOLD", 0.8))
        self.mm_stale_quote_seconds = int(os.getenv("MM_STALE_QUOTE_SECONDS", 30))
        self.mm_requote_threshold = float(os.getenv("MM_REQUOTE_THRESHOLD", 0.5))
        self.mm_post_only = os.getenv("MM_POST_ONLY", "true").lower() in ("true", "1", "yes")
        self.mm_cross_reject_threshold = int(os.getenv("MM_CROSS_REJECT_THRESHOLD", 3))
        self.mm_cross_cooldown_seconds = int(os.getenv("MM_CROSS_COOLDOWN_SECONDS", 300))
        self.mm_cross_cooldown_max_seconds = int(os.getenv("MM_CROSS_COOLDOWN_MAX_SECONDS", 600))
        self.mm_min_depth_usd = float(os.getenv("MM_MIN_DEPTH_USD", 500.0))
        self.mm_min_activity_per_min = float(os.getenv("MM_MIN_ACTIVITY_PER_MIN", 1.0))
        self.mm_max_markets = int(os.getenv("MM_MAX_MARKETS", 10))
        self.mm_scanner_refresh_minutes = int(os.getenv("MM_SCANNER_REFRESH_MINUTES", 5))
        self.mm_dd_reduce_pct = float(os.getenv("MM_DD_REDUCE_PCT", 15.0))
        self.mm_dd_kill_pct = float(os.getenv("MM_DD_KILL_PCT", 25.0))
        self.mm_dd_resume_pct = float(os.getenv("MM_DD_RESUME_PCT", 20.0))
        self.mm_dd_cooldown_minutes = float(os.getenv("MM_DD_COOLDOWN_MINUTES", 30.0))
        self.mm_dd_max_recoveries_per_day = int(os.getenv("MM_DD_MAX_RECOVERIES_PER_DAY", 3))
        self.mm_adverse_selection_window = int(os.getenv("MM_ADVERSE_SELECTION_WINDOW", 120))
        self.mm_early_market_hours = int(os.getenv("MM_EARLY_MARKET_HOURS", 48))
        self.mm_early_market_min_depth_usd = float(os.getenv("MM_EARLY_MARKET_MIN_DEPTH_USD", 100.0))
        self.mm_early_market_min_activity = float(os.getenv("MM_EARLY_MARKET_MIN_ACTIVITY", 0.1))
        self.mm_early_market_boost = int(os.getenv("MM_EARLY_MARKET_BOOST", 3))
        self.mm_early_market_max_slots = int(os.getenv("MM_EARLY_MARKET_MAX_SLOTS", 3))
        self.mm_two_sided = os.getenv("MM_TWO_SIDED", "true").lower() in ("true", "1", "yes")
        self.mm_use_split_merge = os.getenv("MM_USE_SPLIT_MERGE", "true").lower() in ("true", "1", "yes")
        self.mm_split_size_usd = float(os.getenv("MM_SPLIT_SIZE_USD", 5.0))
        self.mm_merge_threshold = float(os.getenv("MM_MERGE_THRESHOLD", 10.0))
        self.mm_min_quote_lifetime_seconds = int(os.getenv("MM_MIN_QUOTE_LIFETIME_SECONDS", 10))
        self.mm_cancel_price_threshold = float(os.getenv("MM_CANCEL_PRICE_THRESHOLD", 1.5))
        self.mm_cancel_size_threshold = float(os.getenv("MM_CANCEL_SIZE_THRESHOLD", 25.0))
        self.mm_arb_enabled = os.getenv("MM_ARB_ENABLED", "false").lower() in ("true", "1", "yes")
        self.mm_arb_min_profit_pct = float(os.getenv("MM_ARB_MIN_PROFIT_PCT", 0.5))
        self.mm_arb_max_size_usd = float(os.getenv("MM_ARB_MAX_SIZE_USD", 50.0))
        self.mm_arb_gas_cost_usd = float(os.getenv("MM_ARB_GAS_COST_USD", 0.005))
        self.mm_scorer_enabled = os.getenv("MM_SCORER_ENABLED", "false").lower() in ("true", "1", "yes")
        self.mm_scorer_min_score = float(os.getenv("MM_SCORER_MIN_SCORE", 5.0))
        self.mm_scorer_cache_minutes = int(os.getenv("MM_SCORER_CACHE_MINUTES", 10))
        # Phase 5A
        self.mm_max_spread_pts = float(os.getenv("MM_MAX_SPREAD_PTS", 12.0))
        self.mm_scanner_concurrency = int(os.getenv("MM_SCANNER_CONCURRENCY", 10))
        self.mm_stale_threshold_seconds = float(os.getenv("MM_STALE_THRESHOLD_SECONDS", 60.0))
        # Phase 5B
        self.mm_pricing_engine = os.getenv("MM_PRICING_ENGINE", "as")
        self.mm_as_gamma_base = float(os.getenv("MM_AS_GAMMA_BASE", 0.1))
        self.mm_as_gamma_alpha = float(os.getenv("MM_AS_GAMMA_ALPHA", 0.5))
        self.mm_as_kappa_default = float(os.getenv("MM_AS_KAPPA_DEFAULT", 1.5))
        self.mm_as_kappa_window_minutes = int(os.getenv("MM_AS_KAPPA_WINDOW_MINUTES", 60))
        # Phase 5C
        self.mm_multi_level_count = int(os.getenv("MM_MULTI_LEVEL_COUNT", 1))
        self.mm_level_spread_mult = float(os.getenv("MM_LEVEL_SPREAD_MULT", 1.5))
        self.mm_level_size_mult = float(os.getenv("MM_LEVEL_SIZE_MULT", 2.0))
        self.mm_hanging_orders = os.getenv("MM_HANGING_ORDERS", "true").lower() in ("true", "1", "yes")
        self.mm_vol_widen_threshold = float(os.getenv("MM_VOL_WIDEN_THRESHOLD", 5.0))
        self.mm_event_risk_widen_pct = float(os.getenv("MM_EVENT_RISK_WIDEN_PCT", 50.0))
        # Phase 5D
        self.mm_circuit_breaker_threshold = int(os.getenv("MM_CIRCUIT_BREAKER_THRESHOLD", 5))
        self.mm_circuit_breaker_cooldown = int(os.getenv("MM_CIRCUIT_BREAKER_COOLDOWN", 300))
        self.mm_as_feedback_enabled = os.getenv("MM_AS_FEEDBACK_ENABLED", "true").lower() in ("true", "1", "yes")
        self.mm_as_feedback_threshold_bps = float(os.getenv("MM_AS_FEEDBACK_THRESHOLD_BPS", 50.0))
        self.mm_cd_synergy_enabled = os.getenv("MM_CD_SYNERGY_ENABLED", "false").lower() in ("true", "1", "yes")
        self.mm_cd_synergy_weight = float(os.getenv("MM_CD_SYNERGY_WEIGHT", 0.3))
        self.mm_stoploss_max_spread_pts = float(os.getenv("MM_STOPLOSS_MAX_SPREAD_PTS", 8.0))


@dataclass
class CryptoDirectionalConfig:
    """Crypto directional strategy (Student-t on BTC/ETH price threshold markets)."""
    cd_enabled: bool = True
    cd_cycle_minutes: int = 15
    # Student-t parameters
    cd_student_t_nu: float = 6.0
    cd_ewma_lambda: float = 0.94
    cd_ewma_span: int = 30
    # Edge & sizing
    cd_min_edge_pts: float = 5.0
    cd_confirmation_cycles: int = 2
    cd_kelly_fraction: float = 0.25
    cd_max_position_pct: float = 5.0
    cd_post_only: bool = True
    # Data source
    cd_coingecko_api: str = "https://api.coingecko.com/api/v3"
    # Exit management
    cd_exit_enabled: bool = True
    cd_exit_stop_loss_pts: float = 15.0
    cd_exit_take_profit_pts: float = 20.0
    cd_exit_edge_reversal_pts: float = -3.0
    cd_exit_check_seconds: int = 120
    cd_exit_ai_confirm_enabled: bool = True
    # NL market parsing (Sonnet)
    cd_nl_parsing_enabled: bool = True
    # Post-trade analysis (Opus)
    cd_analysis_enabled: bool = True
    cd_analysis_interval_hours: float = 6.0
    cd_analysis_auto_apply: bool = False
    # Position limits
    cd_max_concurrent_positions: int = 5
    # Pre-trade AI validation (Haiku)
    cd_pretrade_ai_enabled: bool = True

    def __post_init__(self):
        self.cd_enabled = os.getenv("CD_ENABLED", "true").lower() in ("true", "1", "yes")
        self.cd_cycle_minutes = int(os.getenv("CD_CYCLE_MINUTES", 15))
        self.cd_student_t_nu = float(os.getenv("CD_STUDENT_T_NU", 6.0))
        self.cd_ewma_lambda = float(os.getenv("CD_EWMA_LAMBDA", 0.94))
        self.cd_ewma_span = int(os.getenv("CD_EWMA_SPAN", 30))
        self.cd_min_edge_pts = float(os.getenv("CD_MIN_EDGE_PTS", 5.0))
        self.cd_confirmation_cycles = int(os.getenv("CD_CONFIRMATION_CYCLES", 2))
        self.cd_kelly_fraction = float(os.getenv("CD_KELLY_FRACTION", 0.25))
        self.cd_max_position_pct = float(os.getenv("CD_MAX_POSITION_PCT", 5.0))
        self.cd_post_only = os.getenv("CD_POST_ONLY", "true").lower() in ("true", "1", "yes")
        self.cd_coingecko_api = os.getenv("CD_COINGECKO_API", "https://api.coingecko.com/api/v3")
        self.cd_exit_enabled = os.getenv("CD_EXIT_ENABLED", "true").lower() in ("true", "1", "yes")
        self.cd_exit_stop_loss_pts = float(os.getenv("CD_EXIT_STOP_LOSS_PTS", 15.0))
        self.cd_exit_take_profit_pts = float(os.getenv("CD_EXIT_TAKE_PROFIT_PTS", 20.0))
        self.cd_exit_edge_reversal_pts = float(os.getenv("CD_EXIT_EDGE_REVERSAL_PTS", -3.0))
        self.cd_exit_check_seconds = int(os.getenv("CD_EXIT_CHECK_SECONDS", 120))
        self.cd_exit_ai_confirm_enabled = os.getenv("CD_EXIT_AI_CONFIRM_ENABLED", "true").lower() in ("true", "1", "yes")
        self.cd_nl_parsing_enabled = os.getenv("CD_NL_PARSING_ENABLED", "true").lower() in ("true", "1", "yes")
        self.cd_analysis_enabled = os.getenv("CD_ANALYSIS_ENABLED", "true").lower() in ("true", "1", "yes")
        self.cd_analysis_interval_hours = float(os.getenv("CD_ANALYSIS_INTERVAL_HOURS", 6.0))
        self.cd_analysis_auto_apply = os.getenv("CD_ANALYSIS_AUTO_APPLY", "false").lower() in ("true", "1", "yes")
        self.cd_max_concurrent_positions = int(os.getenv("CD_MAX_CONCURRENT_POSITIONS", 5))
        self.cd_pretrade_ai_enabled = os.getenv("CD_PRETRADE_AI_ENABLED", "true").lower() in ("true", "1", "yes")


@dataclass
class ClaudeGuardConfig:
    """Claude guard-fou parameters (minimal AI usage)."""
    guard_enabled: bool = True
    guard_interval_minutes: int = 5
    guard_max_calls_per_hour: int = 12
    guard_news_enabled: bool = True
    guard_news_max_headlines: int = 5
    guard_news_cache_minutes: int = 30

    def __post_init__(self):
        self.guard_enabled = os.getenv("GUARD_ENABLED", "true").lower() in ("true", "1", "yes")
        self.guard_interval_minutes = int(os.getenv("GUARD_INTERVAL_MINUTES", 5))
        self.guard_max_calls_per_hour = int(os.getenv("GUARD_MAX_CALLS_PER_HOUR", 12))
        self.guard_news_enabled = os.getenv("GUARD_NEWS_ENABLED", "true").lower() in ("true", "1", "yes")
        self.guard_news_max_headlines = int(os.getenv("GUARD_NEWS_MAX_HEADLINES", 5))
        self.guard_news_cache_minutes = int(os.getenv("GUARD_NEWS_CACHE_MINUTES", 30))


@dataclass
class AppConfig:
    polymarket: PolymarketConfig
    anthropic: AnthropicConfig
    telegram: TelegramConfig
    trading: TradingConfig
    mm: MarketMakingConfig
    cd: CryptoDirectionalConfig
    guard: ClaudeGuardConfig

    @classmethod
    def load(cls) -> "AppConfig":
        return cls(
            polymarket=PolymarketConfig(),
            anthropic=AnthropicConfig(),
            telegram=TelegramConfig(),
            trading=TradingConfig(),
            mm=MarketMakingConfig(),
            cd=CryptoDirectionalConfig(),
            guard=ClaudeGuardConfig(),
        )
