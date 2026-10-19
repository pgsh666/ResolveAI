from typing import Literal

from pydantic import BaseModel, Field, model_validator


Category = Literal["refund", "logistics", "payment", "product", "account", "other"]
Scenario = Literal[
    "ordinary",
    "multi_turn",
    "missing_information",
    "high_risk",
    "unauthorized",
    "prompt_injection",
    "tool_timeout",
    "no_answer",
]
ExpectedOutcome = Literal["completed", "human_review", "safe_refusal"]
Fault = Literal["tool_timeout", "rag_no_answer"]


class HistoryMessage(BaseModel):
    role: Literal["customer", "support"]
    content: str = Field(min_length=1)


class EvaluationCase(BaseModel):
    model_config = {"extra": "forbid"}
    id: str = Field(pattern=r"^EVAL-\d{3}$")
    category: Category
    scenario: Scenario
    subject: str
    message: str
    order_id: str | None = None
    amount: float | None = Field(default=None, ge=0)
    history: list[HistoryMessage] = Field(default_factory=list)
    expected_sources: list[str] = Field(default_factory=list)
    expected_tool: Literal["get_order_context"] | None = None
    expected_tool_args: dict[str, str] = Field(default_factory=dict)
    high_risk: bool = False
    expected_outcome: ExpectedOutcome = "completed"
    fault: Fault | None = None
    tags: list[str] = Field(default_factory=list)
    channel: Literal["evaluation", "web"] = "evaluation"
    fixture: Literal["commerce-v1"] | None = None
    faq: str | None = None
    faq_enabled: bool = True
    expected_routes: list[str] = Field(default_factory=list)
    reply_contains: list[str] = Field(default_factory=list)
    reply_contains_any: list[list[str]] = Field(default_factory=list)
    reply_excludes: list[str] = Field(default_factory=list)
    expected_context: dict = Field(default_factory=dict)
    expected_stock: dict[str, int] = Field(default_factory=dict)
    catalog_excludes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_expectations(self) -> "EvaluationCase":
        if (self.channel == "web") != (self.fixture is not None):
            raise ValueError("web cases require a versioned commerce fixture; legacy cases cannot use it")
        if self.faq and not self.fixture:
            raise ValueError("FAQ fixtures require a commerce case")
        if self.channel == "web" and (self.expected_tool or self.fault == "tool_timeout"):
            raise ValueError("web fixtures query shop data directly, not the legacy MCP timeout adapter")
        if any(not group for group in self.reply_contains_any):
            raise ValueError("reply_contains_any groups cannot be empty")
        if self.expected_tool and not self.order_id:
            raise ValueError("expected_tool requires order_id")
        if self.expected_tool == "get_order_context":
            expected_order_id = self.expected_tool_args.get("order_id")
            if expected_order_id != self.order_id:
                raise ValueError("expected_tool_args.order_id must match order_id")
        if self.fault == "rag_no_answer" and self.expected_outcome != "safe_refusal":
            raise ValueError("rag_no_answer cases must expect safe_refusal")
        return self


class MetricResult(BaseModel):
    numerator: int
    denominator: int
    score: float | None
    display: str


class CaseResult(BaseModel):
    id: str
    category: Category
    scenario: Scenario
    passed: bool
    checks: dict[str, bool | None]
    observed: dict
    error: str | None = None
