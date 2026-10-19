from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Citation(BaseModel):
    id: str
    title: str
    source: str
    snippet: str
    score: float = Field(ge=0, le=1)


class AuditEventOut(ORMModel):
    id: str
    agent: str
    action: str
    summary: str
    status: str
    duration_ms: int
    details: dict[str, Any] | None = None
    created_at: datetime


class AgentRunOut(BaseModel):
    id: str
    ticket_id: str
    ticket_no: str
    agent: str
    action: str
    summary: str
    status: str
    duration_ms: int
    created_at: datetime


class KnowledgeDocumentOut(ORMModel):
    id: str
    title: str
    source: str
    content: str
    category: str
    enabled: bool = True
    created_at: datetime


class KnowledgeImportItem(BaseModel):
    title: str = Field(min_length=2, max_length=200)
    source: str = Field(min_length=3, max_length=300)
    content: str = Field(min_length=10, max_length=20_000)
    category: str = Field(default="general", min_length=2, max_length=50)


class KnowledgeImportRequest(BaseModel):
    documents: list[KnowledgeImportItem] = Field(min_length=1, max_length=100)


class BatchEvaluationRequest(BaseModel):
    limit: int | None = Field(default=None, ge=1, le=120)


class BackgroundTaskOut(BaseModel):
    id: str
    task_type: Literal["agent_analysis", "knowledge_import", "batch_evaluation"]
    status: Literal[
        "queued",
        "started",
        "finished",
        "failed",
        "deferred",
        "scheduled",
        "stopped",
        "canceled",
        "rate_limited",
        "ready_to_enqueue",
    ]
    progress: int = Field(ge=0, le=100)
    stage: str
    message: str
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: datetime | None = None
    started_at: datetime | None = None
    updated_at: datetime | None = None
    finished_at: datetime | None = None
    event_url: str | None = None


class ApprovalOut(ORMModel):
    id: str
    state: str
    action: str
    amount: float | None = Field(default=None, ge=0)
    risk_reason: str | None = None
    reviewer_note: str | None = None
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None


class TicketBase(BaseModel):
    customer_name: str = Field(min_length=1, max_length=100)
    customer_tier: str = "standard"
    channel: str = "chat"
    subject: str = Field(min_length=1, max_length=200)
    message: str = Field(min_length=2, max_length=4000)
    priority: str = "medium"
    order_id: str | None = Field(
        default=None,
        min_length=2,
        max_length=30,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]+$",
    )
    amount: float | None = Field(default=None, ge=0)


class TicketCreate(TicketBase):
    pass


class CustomerTicketCreate(BaseModel):
    customer_name: str | None = Field(default=None, min_length=1, max_length=100)
    subject: str | None = Field(default=None, min_length=1, max_length=200)
    message: str = Field(min_length=1, max_length=4000)
    order_id: str | None = Field(
        default=None,
        min_length=2,
        max_length=30,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]+$",
    )
    amount: float | None = Field(default=None, ge=0)


class CustomerConfirmationRequest(BaseModel):
    resolved: bool
    rating: int | None = Field(default=None, ge=1, le=5)
    feedback: str = Field(default="", max_length=500)


class CustomerMessageRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


class ConversationMessage(BaseModel):
    id: str
    role: Literal["customer", "support"]
    content: str
    created_at: datetime


class CustomerTicketOut(BaseModel):
    id: str
    ticket_no: str
    customer_name: str
    subject: str
    message: str
    stage: Literal["submitted", "processing", "reviewing", "answered", "resolved", "follow_up"]
    sent_response: str | None = None
    sent_at: datetime | None = None
    customer_confirmed: bool | None = None
    customer_rating: int | None = None
    customer_feedback: str | None = None
    messages: list[ConversationMessage] = Field(default_factory=list)
    handed_off: bool = False
    handoff_reasons: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class TicketSummary(ORMModel):
    case_type: Literal["consultation", "after_sales"] = "consultation"
    id: str
    ticket_no: str
    legacy_ticket_no: str | None = None
    customer_name: str
    customer_tier: str
    channel: str
    subject: str
    status: str
    priority: str
    category: str | None = None
    sentiment: str | None = None
    order_id: str | None = None
    amount: float | None = None
    sla_minutes: int
    confidence: float | None = None
    recommended_action: str | None = None
    requires_approval: bool
    created_at: datetime
    updated_at: datetime


class CustomerFeedbackOut(BaseModel):
    id: str
    resolved: bool | None = None
    rating: int | None = Field(default=None, ge=1, le=5)
    feedback: str = ""
    created_at: datetime | None = None


class TicketDetail(TicketSummary):
    message: str
    draft_response: str | None = None
    sent_response: str | None = None
    sent_at: datetime | None = None
    resolution: str | None = None
    assessment: dict[str, Any] | None = None
    citations: list[Citation] = []
    audit_events: list[AuditEventOut] = []
    approval: ApprovalOut | None = None
    conversation: list[ConversationMessage] = Field(default_factory=list)
    customer_feedbacks: list[CustomerFeedbackOut] = Field(default_factory=list)


class DecisionRequest(BaseModel):
    decision: Literal["approve", "reject"]
    note: str = ""
    reviewer: str = "产品经理"


class DraftRewriteRequest(BaseModel):
    instruction: str = "保持政策与金额不变，改写得更自然、简洁、有同理心"


class DraftSendRequest(BaseModel):
    response: str = Field(min_length=1, max_length=4000)


class DraftRewriteResult(BaseModel):
    draft_response: str = Field(min_length=1, max_length=4000)


class ToolExecutionRequest(BaseModel):
    idempotency_key: str = Field(
        min_length=8,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]+$",
        description="调用方生成的唯一幂等键；同一业务动作重试时必须保持不变",
    )
    arguments: dict[str, Any]


class ToolExecutionOut(ORMModel):
    id: str
    ticket_id: str
    approval_id: str
    tool_name: str
    tool_kind: str
    idempotency_key: str
    arguments: dict[str, Any]
    result: dict[str, Any] | None = None
    status: str
    error: str | None = None
    executed_by: str
    replayed: bool = False
    created_at: datetime
    completed_at: datetime | None = None


class WorkflowCheckpointOut(ORMModel):
    id: str
    ticket_id: str
    thread_id: str
    checkpoint_id: str | None = None
    current_node: str
    next_node: str | None = None
    status: str
    state: dict[str, Any]
    tool_results: list[dict[str, Any]]
    conversation: list[dict[str, Any]]
    audit_count: int
    version: int
    resumed_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class HealthResponse(BaseModel):
    status: str
    app: str
    agent_mode: str
    model: str
    database: str
    database_ready: bool
    knowledge_documents: int
    mcp_enabled: bool
    checks: dict[str, str]


class Metric(BaseModel):
    label: str
    value: str
    change: str
    tone: Literal["positive", "neutral", "warning"]


class TrendPoint(BaseModel):
    day: str
    automated: int
    manual: int


class CategoryMetric(BaseModel):
    name: str
    value: int
    color: str


class DashboardResponse(BaseModel):
    metrics: list[Metric]
    trend: list[TrendPoint]
    categories: list[CategoryMetric]
    pending_approvals: int
    agent_success_rate: float


class TriageResult(BaseModel):
    category: Literal["refund", "logistics", "payment", "account", "product", "other"]
    priority: Literal["low", "medium", "high", "urgent"]
    sentiment: Literal["positive", "neutral", "negative", "angry"]
    intent: str
    confidence: float = Field(ge=0, le=1)


class ResolutionResult(BaseModel):
    recommended_action: str
    rationale: str
    draft_response: str
    refund_amount: float | None = None
    requires_approval: bool
    confidence: float = Field(ge=0, le=1)


class ProductInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=4000)
    image: str = Field(default="", max_length=2_800_000)
    price: float = Field(ge=0.01, le=1_000_000, multiple_of=0.01, allow_inf_nan=False)
    stock: int = Field(ge=0, le=1_000_000)
    category: str = Field(default="其他", min_length=1, max_length=50)
    active: bool = True


class OrderCreate(BaseModel):
    product_id: str
    quantity: int = Field(ge=1, le=100)
    address: str = Field(min_length=2, max_length=500)
    idempotency_key: str = Field(min_length=8, max_length=100)


class OrderAction(BaseModel):
    action: Literal["pay", "cancel", "ship", "advance", "deliver", "sign"]
    transit_days: int | None = Field(default=None, ge=0, le=365)
    expected_delivery_days: int | None = Field(default=None, ge=1, le=30)


class KnowledgeToggle(BaseModel):
    enabled: bool
