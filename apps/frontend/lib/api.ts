export type Paginated<T> = {
  count: number;
  next: string | null;
  previous: string | null;
  results: T[];
};

export type Overview = {
  available_usdc: number;
  onchain_balance: number | null;
  positions_count: number;
  daily_pnl: number;
  daily_traded: number;
  total_invested: number;
  portfolio_value: number;
  total_pnl: number;
  roi_percent: number;
  hit_rate: number;
  total_trades: number;
  bot_status: string;
  is_paper: boolean;
  strategy: string;
  cycle_number: number;
  cycle_interval_minutes: number;
};

export type BotCommand = {
  id: number;
  command: string;
  source: string;
  status: string;
  payload: Record<string, unknown>;
  result: Record<string, unknown> | null;
  created_at: string;
  executed_at: string | null;
};

export type Position = {
  id: number;
  market_id: string;
  token_id: string;
  outcome: string;
  size: string;
  avg_price: string;
  status: string;
  strategy: string;
  category: string;
  updated_at: string;
};

export type Trade = {
  id: number;
  market_id: string;
  side: string;
  outcome: string;
  size_usdc: string;
  price: string;
  status: string;
  strategy: string;
  created_at: string;
};

export type PerformanceSnapshot = {
  id: number;
  snapshot_type: string;
  payload: Record<string, unknown>;
  created_at: string;
};

export type BotSetting = {
  id: number;
  key: string;
  value: string;
  metadata: Record<string, unknown>;
  updated_at: string;
};

export type LearningJournalEntry = {
  id: number;
  legacy_id: number | null;
  cycle_number: number;
  trades_proposed: number;
  trades_executed: number;
  trades_skipped: number;
  skipped_markets: string;
  retrospective_json: string;
  price_snapshots: string;
  outcome_accuracy: number | null;
  created_at: string;
  updated_at: string;
};

export type LearningInsight = {
  id: number;
  legacy_id: number | null;
  insight_type: string;
  description: string;
  evidence: string;
  proposed_action: string;
  severity: string;
  status: string;
  created_at: string;
  updated_at: string;
};

export type LearningProposal = {
  id: number;
  legacy_id: number | null;
  proposal_type: string;
  target: string;
  current_value: string;
  proposed_value: string;
  rationale: string;
  risk_level: string;
  status: string;
  applied_at: string | null;
  created_at: string;
  updated_at: string;
};

export type ManagerCritique = {
  id: number;
  legacy_id: number | null;
  cycle_number: number;
  critique_json: string;
  summary: string;
  trading_quality_score: number | null;
  risk_management_score: number | null;
  strategy_effectiveness_score: number | null;
  improvement_areas: string;
  code_changes_suggested: string;
  status: string;
  developer_result: string;
  branch_name: string;
  commit_hash: string;
  deploy_status: string;
  user_feedback: string;
  reviewed_at: string | null;
  deployed_at: string | null;
  created_at: string;
  updated_at: string;
};

export type LearningGitChange = {
  id: number;
  legacy_id: number | null;
  proposal_legacy_id: number | null;
  branch_name: string;
  commit_hash: string;
  remote_name: string;
  push_status: string;
  justification: string;
  files_changed: string[];
  result: Record<string, unknown>;
  created_at: string;
  updated_at: string;
};

export type OrderEvent = {
  id: number;
  event_type: string;
  status: string;
  order_id: string | null;
  created_at: string;
  note: string;
};

function resolveBaseUrl(): string {
  const fromEnv = process.env.NEXT_PUBLIC_CONTROL_PLANE_URL?.trim();
  if (fromEnv) {
    return fromEnv.replace(/\/+$/, "");
  }

  if (typeof window !== "undefined") {
    return `${window.location.origin}/api/v1`;
  }

  return "http://127.0.0.1:8000/api/v1";
}

function buildHeaders(init?: HeadersInit): Headers {
  const headers = new Headers(init ?? {});
  if (!headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const token = process.env.NEXT_PUBLIC_CONTROL_PLANE_TOKEN;
  if (token && !headers.has("Authorization")) {
    headers.set("Authorization", `Token ${token}`);
  }
  return headers;
}

export async function cpFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${resolveBaseUrl()}${path}`, {
    ...init,
    credentials: "include",
    headers: buildHeaders(init?.headers),
    cache: "no-store",
  });

  if (!response.ok) {
    const body = await response.text();
    throw new Error(`Control-plane ${response.status}: ${body}`);
  }

  return (await response.json()) as T;
}

export function asPaginated<T>(payload: Paginated<T> | T[]): T[] {
  if (Array.isArray(payload)) {
    return payload;
  }
  return payload.results;
}

// Risk Officer Reviews
export interface RiskOfficerReview {
  id: number;
  legacy_id: number | null;
  cycle_number: number | null;
  review_json: string;
  portfolio_risk_summary: string;
  trades_reviewed: number;
  trades_flagged: number;
  trades_rejected: number;
  parameter_recommendations: Record<string, unknown>[];
  created_at: string;
}

// Strategist Assessments
export interface StrategistAssessment {
  id: number;
  legacy_id: number | null;
  assessment_json: string;
  summary: string;
  market_regime: string;
  regime_confidence: number;
  allocation_score: number | null;
  diversification_score: number | null;
  category_allocation: Record<string, unknown>;
  recommendations: Record<string, unknown>[];
  strategic_insights: string[];
  created_at: string;
}

// Chat Messages
export interface ChatMessage {
  id: number;
  legacy_id: number | null;
  source: string;
  role: string;
  agent_name: string;
  message: string;
  action_taken: Record<string, unknown> | null;
  created_at: string;
}

// File Change Audit
export interface FileChangeAudit {
  id: number;
  legacy_id: number | null;
  file_path: string;
  change_type: string;
  tier: number;
  agent_name: string;
  reason: string | null;
  diff_summary: string | null;
  backup_path: string | null;
  status: string;
  created_at: string;
}

export interface MMQuote {
  id: number;
  market_id: string;
  token_id: string;
  bid_order_id: string | null;
  ask_order_id: string | null;
  bid_price: number;
  ask_price: number;
  mid_price: number | null;
  size: number;
  status: string;
  created_at: string;
  updated_at: string;
}

export interface MMInventory {
  id: number;
  market_id: string;
  token_id: string;
  net_position: number;
  avg_entry_price: number;
  unrealized_pnl: number;
  realized_pnl: number;
  updated_at: string;
}

export interface MMDailyMetric {
  id: number;
  date: string;
  markets_quoted: number;
  quotes_placed: number;
  fills_count: number;
  round_trips: number;
  spread_capture_rate: number;
  fill_quality_avg: number;
  adverse_selection_avg: number;
  pnl_gross: number;
  pnl_net: number;
  max_inventory: number;
  inventory_turns: number;
  created_at: string;
}

export interface CDSignal {
  id: number;
  market_id: string;
  token_id: string | null;
  coin: string;
  strike: number;
  expiry_days: number;
  spot_price: number;
  vol_ewma: number;
  p_model: number;
  p_market: number;
  edge_pts: number;
  confirmation_count: number;
  action: string;
  size_usdc: number | null;
  order_id: string | null;
  created_at: string;
}

// API functions
export async function fetchRiskReviews() {
  return cpFetch<RiskOfficerReview[]>("risk-reviews/");
}

export async function fetchStrategistAssessments() {
  return cpFetch<StrategistAssessment[]>("strategist/");
}

export async function fetchChatHistory(sinceId?: number) {
  const params = sinceId ? `?since=${sinceId}` : "";
  return cpFetch<ChatMessage[]>(`chat/history/${params}`);
}

export async function sendChatMessage(message: string) {
  return cpFetch<{ status: string; command_id: number }>("chat/send/", {
    method: "POST",
    body: JSON.stringify({ message }),
  });
}

export async function fetchFileAudit() {
  return cpFetch<FileChangeAudit[]>("audit/");
}

// MM (Market Making) Endpoints
export async function fetchMMQuotes(status?: string) {
  const params = status ? `?status=${status}` : "";
  return cpFetch<Paginated<MMQuote> | MMQuote[]>(`/mm-quotes/${params}`);
}

export async function fetchMMInventory() {
  return cpFetch<Paginated<MMInventory> | MMInventory[]>("/mm-inventory/");
}

export async function fetchMMMetrics() {
  return cpFetch<Paginated<MMDailyMetric> | MMDailyMetric[]>("/mm-metrics/");
}

// CD (Crypto Directional) Endpoints
export async function fetchCDSignals() {
  return cpFetch<Paginated<CDSignal> | CDSignal[]>("/cd-signals/");
}
