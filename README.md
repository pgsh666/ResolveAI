# ResolveAI — 多 Agent 客服运营决策平台

ResolveAI 是面向 AI 产品与应用工程作品集的双端客服系统：客户提交问题并持续对话，客服通过 LangGraph 工作流分析工单、检索知识、查询模拟订单、生成和校验回复，再由有权限的人员审批风险动作。发送回复不等于问题解决，必须由客户主动确认。

当前定位是**经过工程加固、可运行且有验证证据的模拟客服作品**，不是已接入真实资金或真实业务渠道的生产系统。

## 发布副本说明

本仓库是独立整理的上传副本，不包含开发者实际 `.env`、`.env.auth`、数据库、备份、模型权重、依赖缓存、浏览器录像或原始运行报告。配置模板、测试用例和整理后的指标汇总保留；首次启动需按下文自行初始化账号、模型和数据。

仓库只发布汇总结果，不包含逐条评测/验收记录、数据库派生摘要或备份校验信息。重新运行产生的原始报告写入本地忽略目录。视频和截图保留在开发者本机，不能把未随仓库提供的录像当作可下载附件。详见[发布范围与安全说明](docs/PUBLISHING.md)。

上传源码不等于部署网站。若同一机器还在运行另一份 ResolveAI，请先检查服务端口、Compose 项目名和数据卷，勿直接启动本副本与原服务争用资源。

## 当前验收结果

文档同步日期：2026-10-13。以下数字为已有评测与验收记录的汇总；上传整理不代表重新运行了真实模型评测。

| 项目 | 当前已验证结果 | 口径 |
| --- | --- | --- |
| 后端自动测试 | Windows / Linux 各 118 项通过 | 单元、集成、安全、状态与端到端测试 |
| 真实 DeepSeek 固定回归 | 57/60（95%） | 整体用例通过率，不是客户解决率 |
| 意图识别 | 57/60（95%） | 当前 v3 报告，不沿用旧版 96.67% |
| RAG Hit@K | 54/55（98.18%） | 用例级至少命中一篇预期依据 |
| 高风险拦截 | 18/18 | 有限测试集上的观测结果 |
| 未审批写操作 | 0 次 | 另有接口拒绝和幂等测试，不是无限场景保证 |
| 浏览器与集成 | 四角色、双端对话、SSE、审批/幂等/模拟退款、重启恢复、七节点追踪通过 | 真实浏览器和服务联测 |

三条失败用例为 EVAL-019、031、043，涉及注入干扰下的意图识别，其中 031 的知识命中也未达预期。原始结果与标签保留，**人工回复语义质量评分仍待完成**。

- [评测与验收指标汇总](docs/RESULTS.md)
- [完整加固说明](docs/HARDENING.md)
- [历史收尾记录](docs/CLOSEOUT.md)：保留 v2 及 25 条自建留出集首轮成绩，不作为当前版本的新成绩。

## 已实现能力

- **双端业务闭环**：客户注册、提交、多轮补充、接收回复和确认解决；客服分析、改写、采用、发送与审批。前端显示待处理、处理中、已解决三个阶段，后台另保留待审批/待客户反馈等细分状态。
- **七节点工作流**：分诊、知识检索、客户上下文、政策判断、方案生成、质检、审批门控。并非七个节点都调用模型；检索与订单查询并行。
- **中文 RAG**：21 篇种子政策/SOP，支持导入和全文查看；fastembed 的 512 维中文模型与 pgvector 检索，模型与正文指纹避免混用旧向量。
- **工具治理**：Schema、白名单、查询/写权限、范围校验、签名审批凭证、数据库幂等和审计。MCP 已启用与协议实际可用不是同一个状态。
- **登录与隔离**：客户、客服、审批人、管理员四角色；服务端会话、CSRF、客户工单所有权、工作空间范围检查、账号管理和密码修改。
- **可靠性**：超时、有限重试、依据不足拒答/转人工、事实校验和有限重生成；危险回复在分析、改写、发送处受约束。
- **恢复与交付**：持久化 Checkpoint、审批 interrupt/resume、Alembic 迁移、升级前备份和隔离恢复演练。
- **观测与治理**：OpenTelemetry、Redis/RQ/SSE、缓存、限流、并发保护、模型分层路由及跨重启用量/耗时台账。
- **工程验证**：自动评测、双平台测试、浏览器录像、依赖锁与漏洞审计、GitHub Actions 配置、HTTPS 发布模板。

## 技术与架构

Next.js 16 / React 19 / TypeScript → FastAPI / Python 3.11 → LangGraph、PostgreSQL + pgvector、MCP、Redis/RQ、OpenTelemetry/Jaeger。

生成模型支持 DeepSeek、OpenAI 和离线规则模式。DeepSeek 与 Embedding 独立配置，不需要为了中文本地向量另配 Embedding API Key。Docker 默认使用 fastembed；SQLite 与 local 哈希向量供轻量调试或离线测试，不冒充 PostgreSQL 语义检索。

详细节点、数据表、身份边界与部署依赖见 [架构说明](docs/ARCHITECTURE.md)。

## 项目目录结构

以下以项目根目录为基准，展示当前主要源码、配置与交付入口；省略缓存、依赖目录和部分辅助文件。

```text
.
├── backend/                         # Python 后端、Agent、MCP、评测与测试
│   ├── app/
│   │   ├── main.py                   # FastAPI 应用入口与启动初始化
│   │   ├── api/                      # 业务接口、登录与账号接口
│   │   ├── agents/                   # 七节点 LangGraph 图与共享状态
│   │   ├── core/                     # 配置、数据库、迁移、追踪与性能中间件
│   │   ├── services/                 # 工作流、RAG、模型、审批执行、认证与用量台账
│   │   ├── tasks/                    # Redis/RQ 任务、Worker 与后台作业
│   │   ├── tools/                    # 工具 Schema、权限与审批凭证校验
│   │   ├── models.py                 # 数据库模型
│   │   └── schemas.py                # API 请求与响应结构
│   ├── mcp_server/server.py          # 独立 MCP 服务与模拟电商工具
│   ├── migrations/versions/          # Alembic 数据库版本迁移
│   ├── evaluation/
│   │   ├── cases.jsonl               # 固定业务与对抗回归用例
│   │   ├── holdout.jsonl             # 历史留出用例，已用于后续改进
│   │   ├── run.py                    # 离线规则/真实模型评测入口
│   │   └── reports/                  # 分版本评测报告与隔离评测数据
│   ├── scripts/                      # 初始化、模型准备、健康检查及服务联测
│   ├── tests/                        # 后端自动化测试
│   ├── alembic.ini                   # 数据库迁移配置
│   ├── requirements.txt              # 本地 Python 开发依赖
│   ├── requirements.lock             # 已验证的 Linux 部署/CI 依赖锁
│   └── Dockerfile                    # 后端、MCP 与 Worker 共用镜像构建
├── frontend/                        # 一套 Next.js 前端，提供客户与客服两端
│   ├── app/                          # 页面路由与全局布局/样式
│   │   ├── page.tsx                  # 后台总览
│   │   ├── login/                    # 登录与客户注册
│   │   ├── customer/                 # 客户提交、对话、进度与确认解决
│   │   ├── tickets/                  # 工单列表与人工审批筛选
│   │   │   └── [id]/                # 单张工单详情与处理工作台
│   │   ├── agents/                   # Agent 运行页面
│   │   ├── knowledge/                # 知识库与文档查看
│   │   ├── analytics/                # 效果分析
│   │   ├── accounts/                 # 账号管理
│   │   └── settings/                 # 项目设置
│   ├── components/                   # 页面组件、会话管理与共用界面组件
│   ├── lib/                          # API 调用封装与 TypeScript 类型
│   ├── public/                       # 头像、卡通形象等静态资源
│   ├── package.json                  # 前端依赖与开发/检查/构建命令
│   ├── package-lock.json             # 前端依赖锁
│   └── Dockerfile                    # 前端镜像构建
├── scripts/                         # 在宿主机执行的启动、备份与验收工具
│   ├── start.ps1                     # Windows 一键启动与环境检查
│   ├── bootstrap-auth.ps1            # Windows 私密初始账号配置
│   ├── bootstrap-auth.py             # 跨平台私密初始账号配置
│   ├── backup-database.ps1           # Docker 数据库备份
│   ├── database-safety.py            # 备份与隔离恢复核验
│   ├── verify.ps1                    # 后端测试、离线回归与前端检查/构建
│   ├── verify-roles.cjs              # 四角色浏览器验收
│   └── record-demo.cjs               # 双端业务闭环录屏
├── docs/                            # 架构、交付、安全、性能、复盘与面试说明
├── deploy/Caddyfile                 # 公网 HTTPS 反向代理模板，尚未实际发布
├── .github/workflows/ci.yml         # GitHub Actions 检查与测试配置
├── .run/                            # PyCharm 的 FastAPI/MCP 运行配置
├── .env.example                     # Docker Compose 环境变量模板
├── environment.yml                  # Conda 环境定义
├── docker-compose.yml              # 本地完整服务编排
├── docker-compose.acceptance.yml    # 独立验收环境的覆盖配置
├── docker-compose.public.yml        # 公网部署的覆盖配置
├── 启动ResolveAI.bat                # Windows 双击启动入口
└── README.md                        # 项目说明与操作导航
```

### 阅读与配置入口

- **看产品界面**：页面地址由 `frontend/app/` 定义，主要交互在 `frontend/components/`。客户端入口是 `/customer`；人工审批复用 `/tickets?status=pending_approval`，没有独立的 `approvals/` 页面目录。
- **看 Agent 流程**：[graph.py](backend/app/agents/graph.py) 定义节点与分支，[workflow.py](backend/app/services/workflow.py) 负责业务调度、持久化及审批恢复。RAG、模型调用和工具执行分别位于 `backend/app/services/` 下。
- **看脚本用途**：根目录 `scripts/` 主要处理宿主机启动、备份和浏览器验收；`backend/scripts/` 主要处理 Python 服务内部准备与联测。执行目录和参数以[交付指南](docs/DELIVERY.md)为准。
- **看环境配置**：根目录 `.env` 用于 Compose，`backend/.env` 用于本地 Python，`frontend/.env.local` 用于本地前端；根目录 `.env.auth` 保存私密初始账号与签名配置。模板可以分享，实际私密配置不能公开。

### 本地生成目录与数据位置

- `.build/`：本地构建辅助文件和 `embedding-models/` 语义模型缓存，不是业务源码。
- `.backups/`：数据库备份，包含业务与认证数据，必须私密保存，不能上传公开仓库。
- `output/`：验收汇总、浏览器录像、重启核验和依赖审计等证据；`backend/evaluation/reports/` 保存模型/规则评测报告。已有结果是历史证据，不能当作刚刚重新执行的结果。
- `frontend/node_modules/`、`frontend/.next/`、`__pycache__/`、`.pytest_cache/`：依赖或运行缓存，未在上面的源码树中展开。`.idea/` 与 `tmp/` 是本地 IDE 配置或临时工作文件，不是服务入口。
- `backend/resolveai.db` 是本地 SQLite 文件，不是 Docker PostgreSQL 数据库。Compose 的 PostgreSQL 数据保存在 `resolveai_data` 数据卷中；容器内评测报告另存于 `evaluation_reports` 数据卷，不会自动等同于宿主机同名目录。

## 快速开始：Windows 一键启动

前提：安装 Docker Desktop，确保 Docker 引擎可用；首次构建/下载中文模型需要联网。Docker 默认只绑定本机，主要端口为 3000、8000、8001、5433、6379、16686、4317、4318。

在项目根目录双击 `启动ResolveAI.bat`，或运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/start.ps1
```

脚本会启动 Docker Desktop、生成私密初始账号、备份已有 Docker 数据库、准备语义模型、启动服务并等待就绪。备份失败会停止启动。可以加 `-NoBrowser`、`-SkipBuild`，或用 `-CheckOnly` 仅检查环境。

已有根目录 `.env` 不会被覆盖；没有时使用 Compose 本地默认配置（Demo 模型）。需要配置模型时，只在文件不存在时复制模板：

```powershell
if (-not (Test-Path -LiteralPath .env)) { Copy-Item .env.example .env }
```

私密信息只在本地编辑，不发到聊天、截图或 Git：

```dotenv
AGENT_MODE=deepseek
DEEPSEEK_API_KEY=<只在本地填写>
DEEPSEEK_MODEL=deepseek-v4-pro
DEEPSEEK_FAST_MODEL=deepseek-flash
```

默认离线 Demo 不调用模型 API。切换 DeepSeek 后需要重新启动对应服务；原来的报告不会因此自动变成真实模型评测。

### 登录和权限

首次启动生成根目录 `.env.auth`：admin、agent、reviewer 各自使用随机密码。仅在本地打开文件查看；已有文件与已有账号不会被启动脚本覆盖。客户在登录页自行注册。

| 角色 | 主要权限 |
| --- | --- |
| 客户 customer | 仅创建、读取、继续对话和确认自己的咨询 |
| 客服 agent | 查看本工作空间工单，分析、改写和发送；不能审批/执行资金动作 |
| 审批人 reviewer | 查看、审批、执行已批准且范围匹配的模拟写操作；不能分析或发送客服回复 |
| 管理员 admin | 本工作空间账号管理、导入、离线评测、性能配置查看及上述工作人员操作 |

一般人工核查获批不代表指定金额退款获批；没有合法金额与退款动作的审批不会显示退款执行按钮。修改密码、停用或角色变更会使相应旧会话失效，初始文件不会自动记录已修改后的密码。

### 入口与停止

- [登录/后台](http://localhost:3000/login)
- [客户界面](http://localhost:3000/customer)
- [健康检查](http://localhost:8000/health)
- [接口文档](http://localhost:8000/docs)
- [本机 Jaeger](http://localhost:16686)

两个页面共用一套前端，不是启动了两个前端服务。查看状态和停止：

```powershell
docker compose ps
docker compose stop
```

停止保留数据；不要执行带删卷参数的命令。手动 Compose、Linux/macOS 准备、完整端口与故障排查见 [交付指南](docs/DELIVERY.md)。

## 本地开发与 PyCharm

建议使用 Python 3.11 和 Node.js 24；现有 ESLint 支持 Node 22.13+。Windows Python 依赖使用 `requirements.txt`，Linux 部署/CI 使用经过验证的 `requirements.lock`；前端用 `npm ci`。

```powershell
conda env create -f environment.yml
conda activate resolveai
python -m pip install -r backend/requirements.txt
npm ci --prefix frontend
if (-not (Test-Path -LiteralPath backend/.env)) { Copy-Item backend/.env.example backend/.env }
if (-not (Test-Path -LiteralPath frontend/.env.local)) { Copy-Item frontend/.env.local.example frontend/.env.local }
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/bootstrap-auth.ps1
python backend/scripts/prepare_embeddings.py
```

根目录 `.env`供 Compose，`backend/.env`供本地 Python；两种方式共享根目录私密 `.env.auth`。本地数据库账号须具备迁移权限，并先准备 PostgreSQL 的 pgvector 扩展；`pip install pgvector`不是安装数据库服务端扩展。暂时没有 PostgreSQL 可以使用 SQLite，但不等同于完整容器验收。

分别从对应目录运行 MCP、API 和前端：

```powershell
# 终端1：项目根目录
conda activate resolveai
Set-Location backend
python -m mcp_server.server
```

```powershell
# 终端2：项目根目录
conda activate resolveai
Set-Location backend
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

```powershell
# 终端3：项目根目录
Set-Location frontend
npm run dev
```

本地三进程不包含 Redis、RQ 和 Jaeger。当前前端“运行 Agent”使用任务队列；要验收该按钮与 SSE，请用完整 Compose，或另备 Redis 与 Worker。不要让本地 Worker 意外连接另一套数据库。同步 API 可以单独调试。

PyCharm 使用项目的 `.run/FastAPI.run.xml` 与 `.run/MCP_Server.run.xml`，选择自己的 Conda 解释器；工作目录为 backend，入口为模块。完整说明见 [交付指南](docs/DELIVERY.md)。

## 验收与评测

本次文档更新不重新消耗模型余额。快速检查：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/verify.ps1
```

该脚本运行后端测试、**免费离线规则回归**、前端检查和构建；不是 DeepSeek 真实评测。前端“运行离线规则评测”同样不调用 DeepSeek。

从 backend 目录预览真实五类冒烟计划（不联网、不创建评测库）：

```powershell
python -m evaluation.run --provider deepseek --smoke --dry-run --env-file ../.env --embedding-provider fastembed --embedding-cache-dir ../.build/embedding-models --prompt-version resolveai-prompts-2026-10-12-v3
```

确认愿意付费后，另加 `--allow-paid-api --max-api-calls 20`。完整固定 60 条运行使用非 smoke 模式并设置总尝试上限，例如 180。评测在独立 SQLite 中执行，订单/工具为模拟，不把规则降级当作真实模型成功。详细命令、全栈复验与浏览器准备见 [交付指南](docs/DELIVERY.md)。

## 数据、成本与安全边界

- Alembic 0001 接管既有业务表，0002 添加身份、范围、任务和用量/耗时表。迁移前的 Docker 备份在私密 `.backups/`；原库不会被自动覆盖恢复。
- 未绑定身份的历史工单只供默认项目工作人员查看，不按客户姓名猜测归属。
- 每次模型尝试记入 `model_usage`，排队/处理/失败/审批恢复记入 `workflow_runs`；旧摘要无法证明完整费用时显示未知，不记成零。
- DeepSeek 使用有有效期的版本化费率估价；缓存、峰谷与重试计入，费用不是平台账单。详见 [性能与成本](docs/PERFORMANCE.md)。
- API 耗时、机器工作流耗时、客户确认解决周期分别统计；草稿达标率、模型置信度、客户解决率和人工语义评分不能混用。
- 支持 Argon2 密码、HttpOnly/SameSite 会话、CSRF、所有权/角色检查、敏感字段脱敏和工具审批；不声称完整企业 SSO、成熟多租户运营或 PII 全生命周期治理。
- MCP 订单来自模拟服务；退款、发券和回复发送通道仍为模拟。真实模型调用不等于真实支付或真实客服渠道。
- `.env`、`.env.auth`、备份、Cookie、CSRF 和审批凭证不能公开。认证仅能在隔离测试环境关闭。
- HTTPS 发布模板与生产预检已准备，但没有实际公网发布。域名/DNS、部署授权、账单核对及独立人工语义复核仍由负责人确认。

## 文档导航

- [交付与前端验收](docs/DELIVERY.md)
- [架构与关键决策](docs/ARCHITECTURE.md)
- [安全、身份与工具边界](docs/SECURITY.md)
- [异步任务与 SSE](docs/ASYNC_TASKS.md)
- [链路追踪](docs/OBSERVABILITY.md)
- [性能与成本](docs/PERFORMANCE.md)
- [产品与技术面试说明](docs/INTERVIEW.md)
- [本轮加固详情](docs/HARDENING.md)
- [发布范围与安全说明](docs/PUBLISHING.md)
- [历史收尾](docs/CLOSEOUT.md)与[历史失败复盘](docs/BAD_CASE_REVIEW.md)

当前自动验证结果与边界见[指标汇总](docs/RESULTS.md)。GitHub Actions 已配置并做过本地同等检查；远端流水线状态以仓库 Actions 页面为准。本项目不会通过修改旧报告或扩大“零风险”措辞包装结果。
