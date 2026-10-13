# ResolveAI OpenTelemetry 链路追踪

文档同步：2026-10-13。当前接口权限、模型准备和复验入口见[交付指南](DELIVERY.md)。

ResolveAI 使用 OpenTelemetry SDK 生成 Trace，通过 OTLP/HTTP 发送到本地 Jaeger。追踪失败不会阻断客服业务流程。

## Span 层级

```text
resolveai.workflow.analyze / resume_after_approval
├── resolveai.agent.triage_agent
│   └── resolveai.llm.triage
├── resolveai.agent.knowledge_agent
│   └── resolveai.rag.reliability_gate
│       └── resolveai.rag.vector_search
├── resolveai.agent.customer_agent
│   └── resolveai.tool.get_order_context
├── resolveai.agent.policy_agent
├── resolveai.agent.resolution_agent
│   └── resolveai.llm.resolution
├── resolveai.agent.quality_agent
└── resolveai.agent.approval_gate
```

人工审批恢复会产生新的根 Trace，上一条 Trace ID 保存在 `assessment.observability.trace_history`。写工具会产生独立的 `resolveai.tool.execute_write` Trace，并包含底层 MCP 工具 Span。

## 记录字段

- 工作流：工单内部 ID、工单号、操作类型、前后状态、是否需要审批；
- Agent：节点名、执行耗时、失败数量、关键决策与质检结果；
- 模型：提供商、模型、Prompt 版本与哈希、尝试/重试次数、Token、估算成本；
- RAG：查询哈希、检索引擎、阈值、文档 ID/标题/来源/分数；
- 工具：工具名、脱敏参数与结果、MCP 来源、尝试/重试次数、错误事件；
- 异常：异常类型、有限长度错误信息和失败节点。

不会导出原始用户消息、完整 Prompt、API Key、密码、审批令牌或其他密钥。Jaeger 仅用于本地作品演示，当前未配置登录鉴权，不应直接暴露到公网。

这里“没有登录”仅指独立Jaeger，不是ResolveAI应用。应用已有登录/RBAC；`GET /api/v1/observability`配置接口仅管理员可读，工单与Trace链接按工单可见范围返回。Jaeger默认只绑定本机，Trace存储目前非长期持久化，不把它作为费用账本。

模型费用与工作流耗时另写model_usage/workflow_runs，跨后台重启保留，包括重试/改写/解析失败等尝试。Trace是否导出成功不决定台账是否存在；Trace里旧字段也不能补造历史账单。

## Docker 验收

先按交付指南启动完整Compose（含账号与语义模型准备），可以执行 `docker compose ps` 检查。

1. 打开 <http://localhost:3000>，以有权限的工作人员登录，选择工单并运行分析；
2. 从接口响应的 `assessment.observability.trace_url` 直接打开对应 Trace，或进入 <http://localhost:16686>；
3. 在 Jaeger 的 Service 中选择 `resolveai-backend`，点击 **Find Traces**；
4. 展开 Trace，确认工作流、Agent、RAG、模型和工具 Span 的父子关系；
5. 查看 Tags 与 Logs，确认耗时、Token/成本、检索文档、工具参数/结果及错误重试字段。

也可以在服务运行时执行一条黑盒验收命令：

从backend目录运行：

```powershell
python -m scripts.verify_tracing
```

脚本自动使用本机私密测试账号登录；如初始密码已更改，需要当前有效身份。默认API8000/Jaeger16686，对独立环境可设置VERIFY_API_URL为http://127.0.0.1:8002/api/v1，VERIFY_JAEGER_URL为http://127.0.0.1:16687。

脚本会创建专用验收工单、运行 Agent、按 Trace ID 查询 Jaeger，验证 7 个 Agent 节点及 LLM/RAG/工具 Span，并执行敏感内容扫描。

非 Docker 本地调试时，先启动 Jaeger，并在 `backend/.env` 中设置：

```dotenv
OTEL_ENABLED=true
OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=http://127.0.0.1:4318/v1/traces
OTEL_UI_URL=http://localhost:16686
```

模型价格会变化。已识别DeepSeek模型默认使用有有效期的2026-10-12官方费率快照，分缓存与峰谷给出估价区间；未知用量/费率或快照过期记为未知，不能补零。也可根据合同显式配置正费率。以下仅示意字段，占位值不能直接复制进.env，须替换成有效数字：

```dotenv
MODEL_INPUT_COST_PER_MILLION_USD=<本地填写实际正费率>
MODEL_OUTPUT_COST_PER_MILLION_USD=<本地填写实际正费率>
```

显式费率模式按未缓存输入估价；默认快照模式区分缓存Token。triage/rewrite低成本阶段另有FAST_MODEL两项费率。Demo没有API费用，Token为近似值。以上均不是平台实付账单；完整口径见[性能与成本](PERFORMANCE.md)。
