from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class ToolKind(StrEnum):
    QUERY = "query"
    WRITE = "write"


class ToolPermission(StrEnum):
    READ = "tools:read"
    WRITE = "tools:write"


class ToolContractError(ValueError):
    """Base error for a rejected tool call."""


class UnknownToolError(ToolContractError):
    pass


class ToolPermissionError(ToolContractError):
    pass


class StrictToolArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class GetOrderContextArguments(StrictToolArguments):
    order_id: str = Field(
        min_length=2,
        max_length=30,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]+$",
        description="商城订单号，例如 ORD-8821",
    )


class CalculateRefundQuoteArguments(GetOrderContextArguments):
    requested_amount: float = Field(
        gt=0,
        le=100_000,
        description="申请退款金额，单位为人民币元",
    )


class ExecuteRefundArguments(GetOrderContextArguments):
    amount: float = Field(gt=0, le=100_000, description="实际退款金额，单位为人民币元")
    currency: Literal["CNY"] = Field(default="CNY", description="退款币种")
    reason: str = Field(min_length=2, max_length=200, description="退款原因")


class IssueCareCouponArguments(StrictToolArguments):
    customer_id: str = Field(min_length=1, max_length=100, description="客户唯一标识")
    amount: float = Field(ge=1, le=20, description="关怀券金额，范围 1 至 20 元")


class ToolDefinition(BaseModel):
    name: str
    kind: ToolKind
    permission: ToolPermission
    description: str
    arguments_model: type[StrictToolArguments]

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def public_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind.value,
            "permission": self.permission.value,
            "description": self.description,
            "input_schema": self.arguments_model.model_json_schema(),
        }


TOOL_REGISTRY: dict[str, ToolDefinition] = {
    "get_order_context": ToolDefinition(
        name="get_order_context",
        kind=ToolKind.QUERY,
        permission=ToolPermission.READ,
        description="查询订单、支付状态、客户等级和风险标记",
        arguments_model=GetOrderContextArguments,
    ),
    "calculate_refund_quote": ToolDefinition(
        name="calculate_refund_quote",
        kind=ToolKind.QUERY,
        permission=ToolPermission.READ,
        description="计算允许退款的金额并判断是否需要人工审批",
        arguments_model=CalculateRefundQuoteArguments,
    ),
    "execute_refund": ToolDefinition(
        name="execute_refund",
        kind=ToolKind.WRITE,
        permission=ToolPermission.WRITE,
        description="执行已通过人工审批的退款（当前为模拟外部支付服务）",
        arguments_model=ExecuteRefundArguments,
    ),
    "issue_care_coupon": ToolDefinition(
        name="issue_care_coupon",
        kind=ToolKind.WRITE,
        permission=ToolPermission.WRITE,
        description="发放已通过人工审批的关怀券（当前为模拟权益服务）",
        arguments_model=IssueCareCouponArguments,
    ),
}


def get_tool_definition(tool_name: str) -> ToolDefinition:
    definition = TOOL_REGISTRY.get(tool_name)
    if definition is None:
        raise UnknownToolError(f"工具 {tool_name!r} 不在白名单中")
    return definition


def validate_tool_call(
    tool_name: str,
    arguments: dict[str, Any],
    granted_permissions: set[ToolPermission] | set[str],
) -> tuple[ToolDefinition, dict[str, Any]]:
    definition = get_tool_definition(tool_name)
    granted = {ToolPermission(item) for item in granted_permissions}
    if definition.permission not in granted:
        raise ToolPermissionError(
            f"调用工具 {tool_name!r} 需要权限 {definition.permission.value!r}"
        )
    # Pydantic rejects missing fields, out-of-range values and unexpected fields.
    validated = definition.arguments_model.model_validate(arguments)
    return definition, validated.model_dump(mode="json")


def tool_catalog() -> list[dict[str, Any]]:
    return [definition.public_dict() for definition in TOOL_REGISTRY.values()]


__all__ = [
    "CalculateRefundQuoteArguments",
    "ExecuteRefundArguments",
    "GetOrderContextArguments",
    "IssueCareCouponArguments",
    "TOOL_REGISTRY",
    "ToolContractError",
    "ToolKind",
    "ToolPermission",
    "ToolPermissionError",
    "UnknownToolError",
    "ValidationError",
    "get_tool_definition",
    "tool_catalog",
    "validate_tool_call",
]
