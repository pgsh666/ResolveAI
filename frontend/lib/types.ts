export type Tone = "positive" | "neutral" | "warning";

export interface HealthData {
  status: string;
  app: string;
  agent_mode: "demo" | "openai" | "deepseek";
  model: string;
  database: string;
  database_ready: boolean;
  knowledge_documents: number;
  mcp_enabled: boolean;
  checks: Record<string, string>;
}

export interface Metric {
  label: string;
  value: string;
  change: string;
  tone: Tone;
}

export interface TrendPoint {
  day: string;
  automated: number;
  manual: number;
}

export interface DashboardData {
  metrics: Metric[];
  trend: TrendPoint[];
  categories: { name: string; value: number; color: string }[];
  pending_approvals: number;
  agent_success_rate: number;
}

export interface Citation {
  id: string;
  title: string;
  source: string;
  snippet: string;
  score: number;
}

export interface AuditEvent {
  id: string;
  agent: string;
  action: string;
  summary: string;
  status: string;
  duration_ms: number;
  details?: Record<string, unknown> | null;
  created_at: string;
}

export interface AgentRun extends AuditEvent {
  ticket_id: string;
  ticket_no: string;
}

export interface AgentTopology {
  edges: { from: string; to: string }[];
}

export interface KnowledgeDocument {
  id: string;
  title: string;
  source: string;
  content: string;
  category: string;
  enabled: boolean;
  created_at: string;
}

export interface KnowledgeSearchResult {
  query: string;
  results: Citation[];
  cache_hit?: boolean;
}

export interface LatencyMetrics {
  samples: number;
  p50_ms: number;
  p95_ms: number;
  average_ms: number;
  max_ms: number;
}

export interface PerformanceMetrics {
  generated_at: string;
  http: LatencyMetrics & { server_errors: number; by_route: Record<string, LatencyMetrics> };
  workflow: LatencyMetrics & { queue: LatencyMetrics; processing: LatencyMetrics; failed_runs: number; customer_resolution_cycle: LatencyMetrics };
  retrieval_cache: { hits: number; misses: number; hit_rate: number };
  guards: { rate_limit_rejections: number; concurrency_rejections: number };
  models: {
    calls: number;
    total_tokens: number;
    average_tokens_per_call: number;
    estimated_cost_usd: number | null;
    average_cost_per_call_usd: number | null;
    complete_cost_calls: number;
    recorded_average_cost_per_call_usd: number | null;
    recorded_cost_scope: string;
    known_estimated_cost_usd: number;
    unknown_cost_calls: number;
    cost_complete: boolean;
    cost_lower_usd: number | null;
    cost_upper_usd: number | null;
    ledger_calls: number;
    legacy_trace_calls: number;
    by_model: Array<Record<string, string | number | null | boolean>>;
    routing: { primary_model: string; economy_model: string; economy_stages: string[] };
  };
  limits: {
    rate_limit_enabled: boolean;
    requests_per_minute: number;
    max_concurrent_requests: number;
    max_context_chars: number;
    max_conversation_messages: number;
    rag_max_documents: number;
  };
}

export type BackgroundTaskStatus =
  | "queued"
  | "started"
  | "finished"
  | "failed"
  | "deferred"
  | "scheduled"
  | "stopped"
  | "canceled"
  | "rate_limited"
  | "ready_to_enqueue";

export interface BackgroundTask {
  id: string;
  task_type: "agent_analysis" | "knowledge_import" | "batch_evaluation";
  status: BackgroundTaskStatus;
  progress: number;
  stage: string;
  message: string;
  result?: Record<string, unknown> | null;
  error?: string | null;
  created_at?: string | null;
  started_at?: string | null;
  updated_at?: string | null;
  finished_at?: string | null;
  event_url?: string | null;
}

export type KnowledgeImportInput = Pick<KnowledgeDocument, "title" | "source" | "content" | "category">;

export interface Approval {
  id: string;
  state: "pending" | "approved" | "rejected";
  action: string;
  amount?: number | null;
  risk_reason?: string | null;
  reviewer_note?: string | null;
  reviewed_by?: string | null;
  reviewed_at?: string | null;
}

export interface TicketSummary {
  case_type: "consultation" | "after_sales";
  id: string;
  ticket_no: string;
  legacy_ticket_no?: string | null;
  customer_name: string;
  customer_tier: string;
  channel: string;
  subject: string;
  status: string;
  priority: string;
  category?: string | null;
  sentiment?: string | null;
  order_id?: string | null;
  amount?: number | null;
  sla_minutes: number;
  confidence?: number | null;
  recommended_action?: string | null;
  requires_approval: boolean;
  created_at: string;
  updated_at: string;
}

export interface TicketDetail extends TicketSummary {
  message: string;
  draft_response?: string | null;
  sent_response?: string | null;
  sent_at?: string | null;
  resolution?: string | null;
  assessment?: {
    intent?: string;
    rationale?: string;
    refund_amount?: number | null;
    customer_context?: Record<string, unknown>;
    policy?: Record<string, unknown>;
    quality?: { passed: boolean; checks: Record<string, boolean> };
    gate?: { state: string };
    citations?: Citation[];
    confidence_breakdown?: {
      version: string;
      method: "weighted_evidence";
      components: {
        key: string;
        label: string;
        score: number;
        weight: number;
        weighted_score: number;
        evidence: string;
      }[];
      base_score: number;
      penalties: { key: string; label: string; value: number; reason: string }[];
      penalty_total: number;
      final_score: number;
      level: "high" | "medium" | "low";
    };
  } | null;
  citations: Citation[];
  audit_events: AuditEvent[];
  approval?: Approval | null;
  conversation: ConversationMessage[];
  customer_feedbacks: CustomerFeedback[];
}

export interface CustomerFeedback {
  id: string;
  resolved: boolean | null;
  rating: number | null;
  feedback: string;
  created_at: string | null;
}

export type CustomerTicketStage = "submitted" | "processing" | "reviewing" | "answered" | "resolved" | "follow_up";

export interface ConversationMessage {
  id: string;
  role: "customer" | "support";
  content: string;
  created_at: string;
}

export interface CustomerTicketCreateInput {
  customer_name?: string;
  subject?: string;
  message: string;
  order_id?: string | null;
  amount?: number | null;
}

export interface CustomerTicket {
  id: string;
  ticket_no: string;
  customer_name: string;
  subject: string;
  message: string;
  stage: CustomerTicketStage;
  sent_response?: string | null;
  sent_at?: string | null;
  customer_confirmed?: boolean | null;
  customer_rating?: number | null;
  customer_feedback?: string | null;
  messages: ConversationMessage[];
  handed_off: boolean;
  handoff_reasons: string[];
  created_at: string;
  updated_at: string;
}

export interface Product {
  id: string; name: string; description: string; image: string; price: number;
  stock: number; category: string; active: boolean; created_at: string;
}
export type ProductInput = Omit<Product, "id" | "created_at">;
export interface StoreOrder {
  id: string; order_no: string; customer_name: string; product_name: string; quantity: number;
  unit_price: number; total: number; refunded: number; status: string; status_label: string;
  address: string; expected_delivery_days: number; transit_days: number; overdue: boolean; created_at: string;
  events: {id: string; status: string; description: string; created_at: string}[];
  ledger: {id: string; kind: string; amount: number; mode: string; created_at: string}[];
}
export interface QuestionRoute {
  id: string; ticket_id: string; ticket_no: string; message_id: string; question: string;
  request_time: string; finished_at: string | null; outcome: string; route: string;
  api_requests: number; model_requests: number; tool_requests: number; total_ms: number;
  steps: {agent: string; action?: string; summary: string; duration_ms: number; status: string}[];
  degraded: boolean; handed_off: boolean; handoff_reasons: string[]; error: string | null;
}
