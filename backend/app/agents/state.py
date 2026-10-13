from operator import add
from typing import Annotated, Any, TypedDict


class SupportState(TypedDict, total=False):
    ticket: dict[str, Any]
    conversation: list[dict[str, Any]]
    triage: dict[str, Any]
    customer_context: dict[str, Any]
    citations: list[dict[str, Any]]
    rag_status: dict[str, Any]
    policy: dict[str, Any]
    resolution: dict[str, Any]
    quality: dict[str, Any]
    quality_feedback: dict[str, Any]
    security: dict[str, Any]
    generation_attempt: int
    gate: dict[str, Any]
    model_trace: Annotated[list[dict[str, Any]], add]
    audit: Annotated[list[dict[str, Any]], add]
    errors: Annotated[list[str], add]
    failures: Annotated[list[dict[str, Any]], add]
