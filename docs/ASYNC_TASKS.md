# Redis 异步任务与 SSE

文档同步：2026-10-16。启动/账号/模型准备见[交付指南](DELIVERY.md)，当前范围见[指标汇总](RESULTS.md)。任务与运行报告不随公开副本发布。

## 适用范围

ResolveAI 只把耗时或批量操作交给后台 Worker：

- 工单的完整多 Agent 分析；
- 知识文档导入与向量生成；
- 免费离线规则批量评测（默认120条，含旧60条与新增商城60条；可用较小条数检查任务管道）；不是DeepSeek真实评测。

普通查询、普通对话、人工审批和发送回复仍走同步 API，避免为短请求引入不必要的队列延迟。

## 数据流

```text
Next.js ──POST──> FastAPI ──enqueue──> Redis ──dequeue──> RQ Worker
   │                  │                                  │
   └──── EventSource / SSE <────任务状态与进度────────────┘
```

RQ Job 是任务状态的唯一事实来源。`queued`、`started`、`finished`、`failed` 等生命周期状态、进度、阶段、结果和错误摘要都保存在 Redis；浏览器断开不会取消任务，重新访问 `GET /api/v1/tasks/{id}` 仍可读取状态。

Redis任务状态与SQLAlchemy业务状态、Checkpoint、model_usage/workflow_runs各有职责：队列完成不代表客户问题解决，查询未过期任务状态不代表任意Worker宕机都自动续跑。

## API

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| POST | `/api/v1/tasks/agent-analysis/{ticket_id}` | 创建 Agent 分析任务 |
| POST | `/api/v1/tasks/knowledge-import` | 导入 1～100 篇知识文档 |
| POST | `/api/v1/tasks/evaluation` | 运行隔离批量评测 |
| GET | `/api/v1/tasks/{task_id}` | 查询持久化任务状态 |
| GET | `/api/v1/tasks/{task_id}/events` | 订阅 SSE 进度与完成/失败事件 |

SSE 事件类型为 `progress`、`completed` 和 `failed`。每条 `data` 都是完整任务快照，前端无需自己合并增量状态。

## 身份与权限

业务请求需有效登录Cookie，POST另需CSRF。分析任务仅由admin创建，知识导入/离线评测仅admin；任务快照与SSE检查工作空间范围，客户不能读取后台任务。前端EventSource携带凭据，SSE先核验范围再释放数据库连接，不在整个流期间占用SQL连接。

## 数据与隔离

- 知识导入在工作空间范围内按 `source` 更新或新增，私有导入文档有范围记录，不能跨空间覆盖私有内容；
- 批量评测为免费规则模式，在独立子进程/SQLite中运行，不修改开发环境PostgreSQL，也不把规则成绩算成真实DeepSeek成绩；
- 评测报告写入共享 Docker Volume `evaluation_reports`；
- 成功与失败状态默认保留 24 小时，Redis 使用 AOF Volume，容器重建后仍可恢复未过期结果；
- Worker 使用 `resolveai-worker` OpenTelemetry Service 名称，便于在 Jaeger 中区分 API 与后台执行链路。

## 验收

先按交付指南启动完整Compose与初始账号，再从backend目录运行：

```powershell
python -m scripts.verify_async_tasks
```

默认API为127.0.0.1:8000；对独立验收环境先设置 `VERIFY_API_URL=http://127.0.0.1:8002/api/v1`，可用VERIFY_OUTPUT_PATH指定报告。自动登录仅读本机私密测试身份，不打印Cookie/密码；改过初始密码时需使用当前有效身份。

验收脚本会真实创建并等待三类任务，通过 SSE 收集进度，确认 Agent 结果落库、知识文档完成向量化、隔离评测产生指标且未审批写操作次数为 0。

Agent分析若启用DeepSeek会消耗余额；知识导入更新一条专用验收文档，批量3条仅验证免费任务管道。需要严格真实模型成绩时使用evaluation.run的provider=deepseek、付费确认和调用上限。录屏、队列任务运行期间不要并行重启Worker做恢复测试。
