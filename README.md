# ResolveAI — 电商双端与多 Agent 智能客服平台

ResolveAI 是一个可运行的 AI 应用作品：客户注册、登录后直接与电商客服对话，也可以浏览商品、下单、支付和查看物流；商户管理商品与全部订单，接管需要人工处理的聊天和售后问题。项目由 LangGraph 编排七个协作节点，结合商城数据库、RAG 知识库与受控工具生成可追溯回复。

项目未接入真实支付、真实物流或企业业务渠道，不处理真实资金；可运行不等于已经通过商业生产验收。商品事实来自商户录入，系统不会把模型生成的内容当成已经执行的业务操作。

## 当前能力

- **客户与商户双端**：仅 customer 客户和 admin 管理员两个角色；登录后分别进入聊天或后台。用户可持续对话、切换历史、关联自己的订单、评价回复和确认解决。
- **商城与订单**：商品名称、图片、描述、分类、价格、库存管理；浏览、搜索、排序、下单、待支付、已支付、运输、送达、签收及交易流水。金额以整数分保存，下单价独立快照，重复支付与库存释放有状态/幂等检查。
- **聊天与售后分离**：聊天管理显示全部咨询；售后处理只集中实际退款、破损或订单异常，不把普通选购、价格询问或主动转人工自动归为售后。
- **电商客服**：查在售商品、价格比较、详情追问、库存、自己的订单物流、商家优惠 FAQ。普通回复自动发送；资料不足先澄清，不虚构成分、补货日期或优惠。
- **七节点协作**：分诊、知识检索、客户上下文、政策判断、方案生成、质检、审批门控；检索与上下文查询可并行，并非七个节点都调用大模型。
- **知识库**：21 篇通用政策/SOP，支持导入、手动 FAQ、文档启停与来源引用；Embedding 独立于生成模型配置，PostgreSQL 使用 pgvector 语义检索。
- **人工边界**：退款/资金写操作、物流超期、明确要求人工、安全风险及流程异常等进入人工队列。不再因对话超过五轮自动转人工。已接管会话不由机器人持续抢答。
- **反馈与状态**：发送回复不等于解决，只有客户确认后关闭；后台详情展示客户评分、留言、处理结果和时间，追问及再次分析后历史反馈仍保留。
- **逐轮溯源**：每次提问记录时间、路由、API 尝试次数（含重试）、结果、总/各步耗时、降级与人工接管。北京时间工单编号如 `26-10-16-1`；商城订单另带 `ORD-` 前缀。
- **工具与工程治理**：Schema、权限/范围检查、人工审批凭证、幂等、审计、Checkpoint 恢复、超时/重试/降级、Redis/RQ/SSE、OpenTelemetry、成本台账、限流、迁移与备份。

管理员只能维护自己的名称和密码，不能操作客户或其他管理员账号。敏感信息仍需按业务入口脱敏；现有基础权限与自动检查不等于完整企业安全治理。

## 技术栈

| 层次 | 使用技术 |
| --- | --- |
| 前端 | Next.js 16、React 19、TypeScript |
| API / Agent | Python 3.11、FastAPI、LangGraph、Pydantic |
| 数据与检索 | PostgreSQL 16、pgvector、SQLAlchemy、Alembic、fastembed |
| 工具接入 | 独立 MCP Server、受控读写工具 |
| 后台与观测 | Redis、RQ、SSE、OpenTelemetry、Jaeger |
| 交付 | Docker Compose、pytest、Playwright、GitHub Actions 配置 |

支持 DeepSeek、OpenAI 与免费离线规则模式；需要真实模型时使用自己的 API Key。模板中的模型名、价格快照与配置不代表永远有效，费用以供应商实际账单为准。

## 快速启动

推荐安装 **Docker Desktop（或 Docker Engine）和 Docker Compose v2**，首次构建与语义模型下载需要网络。Docker 会启动 PostgreSQL + pgvector、Redis、MCP、Worker、API、前端和 Jaeger，不必再手工安装另一套数据库。

### Windows

启动 Docker 后，在项目根目录双击 `启动ResolveAI.bat`，或执行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/start.ps1
```

脚本准备本机私密账号与语义模型，对已有 Docker 数据库备份，构建并等待服务就绪。修改代码后不要使用 `-SkipBuild`；可用 `-NoBrowser` 避免自动打开页面。

初始管理员凭据仅在本机生成并保存在被忽略的 `.env.auth`；没有公开默认登录密码。客户自行注册。配置模板只含占位值，不能把真实 Key 或密码填入模板后上传。

### Linux / macOS

从项目根目录执行：

```bash
test -f .env || cp .env.example .env
python3 scripts/bootstrap-auth.py
mkdir -p .build/embedding-models
docker compose build backend
docker compose run --rm --no-deps -T -v "$PWD/.build/embedding-models:/app/embedding-models:rw" backend python -m scripts.prepare_embeddings --cache-dir /app/embedding-models
docker compose up -d --build
docker compose ps
```

这组命令面向新环境；升级已有数据库前必须另外备份。详细配置、手动 Compose、本地 Python/Node 开发、健康检查与恢复见[交付指南](docs/DELIVERY.md)。

| 入口 | 默认本机地址 |
| --- | --- |
| 登录 | http://localhost:3000/login |
| 客户聊天 / 商城 / 我的订单 | http://localhost:3000/customer |
| API 文档 / 健康检查 | http://localhost:8000/docs / http://localhost:8000/health |
| PostgreSQL / Redis | 127.0.0.1:5433 / 127.0.0.1:6379 |
| Jaeger | http://localhost:16686 |

同一浏览器普通标签共用会话。双端同时验收请使用独立浏览器配置或无痕窗口；不要混用 localhost 与 127.0.0.1 的 Cookie。

## 项目目录

```text
backend/
  app/               API、认证、七节点 Agent、业务服务、工具契约与数据模型
  migrations/        Alembic 迁移
  mcp_server/        独立 MCP 服务
  evaluation/        评测程序与合成输入；运行报告不发布
  scripts/           初始化、模型准备、健康检查与隔离验收
  tests/             自动化测试；仅使用临时测试库
frontend/
  app/               客户聊天/商城/订单与商户后台页面
  components/        导航、聊天工作台、知识库与分析等组件
  lib/               API、类型、显示与时间工具
  public/            应用使用的头像和卡通静态资源
scripts/             一键启动、账号初始化、备份与浏览器验证
docs/                架构、交付、安全、验收与指标说明
deploy/              HTTPS 发布模板
.github/workflows/   CI 配置
```

`.env`、`.env.auth`、数据库、备份、缓存和运行产物不属于公开源码。公开副本不预置业务工单、聊天、商品或订单；通用知识文档可初始化，业务数据需自行录入。保留的自动化测试和合成评测输入不会在正常启动时导入业务库。发布边界见[公开副本说明](docs/PUBLISHING.md)。

## 验证与指标

2026-10-16 当前开发版 Windows / Linux 后端各 **322 项测试通过**；前端 ESLint、TypeScript/正式构建通过。隔离浏览器覆盖双端主要流程、17 页 × 4 宽度共 68 组布局，以及商品详情、人工回复对齐、评分/留言同步与历史保留。远端 GitHub Actions 是否成功以仓库 Actions 页面为准。

```bash
cd backend
python -m pytest -o addopts= -q
python -m evaluation.run --fail-under 90
```

前端检查从根目录运行 `npm ci --prefix frontend`、`npm run lint --prefix frontend` 和 `npm run build --prefix frontend`。浏览器隔离验收见[商城与客服验收](docs/COMMERCE.md)。

历史客服版本曾完成 60 条真实 DeepSeek 回归：整体通过率与意图准确率均为 57/60（95%），RAG Hit@K 为 54/55（98.18%），高风险拦截 18/18，未审批写操作 0 次。这些是**历史、有限测试集观测**，不是新增商城版本的模型成绩，也不是客户解决率。详情和计算口径见[指标汇总](docs/RESULTS.md)及[评测说明](backend/evaluation/README.md)。

真实 DeepSeek 评测必须明确启用付费、限制调用次数，并与免费规则回归分开报告；本次文档/副本整理不重新调用付费模型。

2026-10-18 修复离线分诊上下文、检索扩展、大额重复扣款转人工及评测配置隔离，主项目 Windows 自动测试 **349 项通过**，公开副本 **352 项通过**（含清洁初始化验证）。在未改预期的旧 60 条集上，草稿流程已达到 **54/60（90%）**；将 6 条退款用例按现有转人工边界更新预期后，当前离线回归 **60/60 通过**，高风险拦截 **18/18**，未审批写操作 **0 次**。CI 仍保留 `--fail-under 90`，不是降低门槛；这也不是新一轮 DeepSeek 成绩。本轮未改前端，Docker 引擎未运行，Linux 容器复验与远端 CI 结果仍待确认。版本差异及历史失败见[指标汇总](docs/RESULTS.md)与[评测说明](backend/evaluation/README.md)。

## 更多说明

- [架构与节点职责](docs/ARCHITECTURE.md)
- [部署、配置与备份](docs/DELIVERY.md)
- [双端功能验收](docs/COMMERCE.md)
- [安全与权限](docs/SECURITY.md)
- [成本与性能口径](docs/PERFORMANCE.md)
- [Redis/RQ 与 SSE](docs/ASYNC_TASKS.md)
- [链路追踪](docs/OBSERVABILITY.md)
- [工程加固及未覆盖边界](docs/HARDENING.md)

公开仓库不包含个人试用记录、逐条评测报告、账号会话、凭据、备份或本机录像。素材使用授权、人工语义复核、实际支付/物流接入和公网部署仍需项目负责人确认。
