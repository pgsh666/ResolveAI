# ResolveAI 交付、启动与验收

文档同步：2026-10-16。当前结果与边界见[指标汇总](RESULTS.md)与[双端验收](COMMERCE.md)。本文件中的复验命令会产生本地运行结果，不随公开仓库发布，也不替换历史模型成绩。

公开副本不携带数据库、用户试用记录或真实凭据，首次启动不生成预置业务工单或商品，只准备结构、通用知识和本机管理员。请先上架商品、再由客户注册和购物。清洁副本不等于清空已有 Docker 数据卷；不要为发布而删除原项目数据。见[发布范围](PUBLISHING.md)。

## 1. 推荐启动：Windows

需要 Docker Desktop/Compose、可用的 Docker 引擎和首次下载网络。若本地 PyCharm/FastAPI/MCP/Next.js 正在占用同一端口，先停止它们。

项目根目录双击 `启动ResolveAI.bat`，或：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/start.ps1
```

顺序：生成私密初始账号 → 对已有 Docker 数据库备份 → 准备中文语义模型 → Alembic 升级与通用知识初始化 → MCP、Redis、Worker、API、前端就绪。Jaeger 独立采集链路。首次构建和下载可能较久；备份或健康检查失败会停止，不误报成功。

- `-NoBrowser`：不打开页面。
- `-SkipBuild`：复用已构建镜像；改过代码后不要用它来期待新代码生效。
- `-CheckOnly`：只检查 Docker/Compose，不初始化账号、备份或启动服务。

根目录 `.env` 为 Docker 配置；`backend/.env` 为本地 Python 配置；两者共享根目录私密 `.env.auth`。已有配置不会被脚本覆盖。

## 2. 手动 Compose / Linux 与 macOS

这条路径需要显式准备账号、模型和旧库备份，不能直接跳过这些步骤。

Windows（以下均从根目录执行，Docker 引擎先启动）：

```powershell
if (-not (Test-Path -LiteralPath .env)) { Copy-Item .env.example .env }
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/bootstrap-auth.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/backup-database.ps1
docker compose build backend
New-Item -ItemType Directory -Force .build/embedding-models | Out-Null
docker compose run --rm --no-deps -T -v "${PWD}/.build/embedding-models:/app/embedding-models:rw" backend python -m scripts.prepare_embeddings --cache-dir /app/embedding-models
docker compose up -d --build
docker compose ps
```

Linux/macOS 首次准备：

```bash
test -f .env || cp .env.example .env
python3 scripts/bootstrap-auth.py
mkdir -p .build/embedding-models
docker compose build backend
docker compose run --rm --no-deps -T -v "$PWD/.build/embedding-models:/app/embedding-models:rw" backend python -m scripts.prepare_embeddings --cache-dir /app/embedding-models
docker compose up -d --build
docker compose ps
```

Linux/macOS 这段首次启动命令不自动备份旧库；升级已有库前先用下文 `database-safety.py` 或自己的受控 PostgreSQL 备份流程。只有新空库可省略旧库备份。语义模型已有有效缓存时，可给准备命令加 `--offline`，不重新下载。

Docker 最终依赖受 `backend/requirements.lock` 约束，前端依赖来自 package-lock。不要将 Windows 原生环境直接安装 Linux 锁文件中的 uvloop 等平台包；Windows 开发使用 requirements.txt。

## 3. 模型、入口与登录

未配置模型时为免费离线 Demo。在本地根目录 `.env` 设置 `AGENT_MODE=deepseek`、`DEEPSEEK_API_KEY`、`DEEPSEEK_MODEL=deepseek-v4-pro`、`DEEPSEEK_FAST_MODEL=deepseek-flash` 可切换；Key 不写入命令、录像和文档。切换后重新启动，真实评测另需明确允许付费。

| 服务 | 默认本机地址/端口 |
| --- | --- |
| 登录 / 客服端 | http://localhost:3000/login |
| 客户端 | http://localhost:3000/customer |
| API / 接口文档 | http://localhost:8000/docs |
| 无需登录的健康检查 | http://localhost:8000/health |
| MCP | 127.0.0.1:8001/mcp |
| Docker PostgreSQL | 127.0.0.1:5433 |
| Redis | 127.0.0.1:6379 |
| Jaeger / OTLP | 127.0.0.1:16686 / 4317 / 4318 |

Docker PostgreSQL 5433 与本机 PostgreSQL 常见的 5432 是不同实例，不能以查看一个实例来判断另一个实例是否写入。客服端与客户端是同一个 Next.js 服务的两个页面。

初始仅 admin，随机密码保存在本地 `.env.auth`，不提供通用默认密码。旧 agent/reviewer 初始化项跳过，已有旧角色停止登录，不自动升级或删除历史记录。客户自行注册。管理员只能改自己的名称/密码，不管理其他账号；修改密码注销该账号旧会话，编辑初始文件不能重置已有数据库账号。

客户端 `/customer`、商城和我的订单仅允许 customer 账号，工作人员访问这些页面会回到后台，客户 API 返回 403。一键启动只打开一个浏览器入口；需要同时验收客户与商户流程时，在另一个浏览器配置或无痕窗口注册/登录客户，普通标签页共用同一 Cookie，不能混用客户和管理员会话。

业务 API 默认需登录 Cookie；写请求还需 `X-CSRF-Token`。前端与验收脚本自动处理，直接粘贴接口 URL 或 Swagger 发送不带凭据的请求可能返回 401/403。使用同一主机名，不要把 localhost 和 127.0.0.1 的浏览器 Cookie 当成同一份登录。

## 4. 前端验收

使用不同浏览器/独立浏览器配置区分角色，普通窗口共享 Cookie，不能代表两名独立用户。

1. 注册客户 A，提交问题；再以客户 B 登录，不能读取或确认 A 的工单。
2. 以管理员登录，通过左侧管理 → 客户管理 → 聊天管理查看全部对话；商品咨询仅标记普通咨询，真实退款/商品异常等进入售后处理。普通咨询转人工仍从聊天管理跟进。运行分析观察 SSE、引用、模拟订单来源、模型/降级轨迹和规则评分。
3. 管理员核对动作与金额再批准，后台保存真实登录审批账号，不能由前端假冒。
4. 只有合法的退款动作与金额获批时才执行模拟退款；一般人工核查获批不构成退款授权。重复提交不能产生第二次同类写操作。
5. 管理员采用/改写/发送回复。尝试发送“我们已联系承运商，正在补发商品”这类无工具依据的行动声明，应该被拒绝且不加入对话。
6. 客户继续追问；发送后仍是处理中，只有客户主动点击“问题已解决”才解决。
7. 侧栏底部“我的账号”仅有自己资料，无客户列表、创建或停用；尝试操作其他账号 API 返回 403。将窗口缩到手机宽度，点击导航应可滚动查看全部三级菜单。管理员还可验收知识导入、离线规则评测、费用区间和耗时分析。
8. 后台发送短回复与长多行回复，气泡及头像右侧对齐；客户提交评分/留言后，在同一详情底部“客户反馈”查看，继续追问及刷新后历史仍保留。

知识库有 21 篇种子政策，导入会使数量增加；不能把“21篇”当成永远固定的总数。评分不是概率校准或人工回复质量分。

## 5. 本地自动验证与免费规则评测

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/verify.ps1
```

也可从 backend 运行 `python -m pytest -q`、`python -m pytest -m integration -q`、`python -m pytest -m e2e -q`；工具、恢复、异常、安全、身份和加固分别见 test_tool_governance、test_checkpoint、test_resilience、test_security、test_auth、test_hardening。

`python -m evaluation.run --fail-under 90` 是独立 SQLite 的免费 Demo 规则回归，门槛针对草稿流程指标，不是付费模型成绩或线上客户解决率。前端批量评测也为此路径。

前端从根目录执行 `npm ci --prefix frontend`、`npm run lint --prefix frontend`、`npm run build --prefix frontend`。

## 6. 真实 DeepSeek 评测

从 backend 目录先预览计划：

```powershell
python -m evaluation.run --provider deepseek --smoke --dry-run --env-file ../.env --embedding-provider fastembed --embedding-cache-dir ../.build/embedding-models --prompt-version resolveai-prompts-2026-10-12-v3
```

允许消耗余额后运行五类冒烟（最多20次 API 尝试，包括重试与重生成）：

```powershell
python -m evaluation.run --provider deepseek --smoke --allow-paid-api --max-api-calls 20 --env-file ../.env --embedding-provider fastembed --embedding-cache-dir ../.build/embedding-models --prompt-version resolveai-prompts-2026-10-12-v3 --output-dir evaluation/reports/revalidation
```

默认完整120条回归（历史60条仍保留在 `evaluation/cases.jsonl`，新版输入为 `evaluation/cases_v3_120.jsonl`）：

```powershell
python -m evaluation.run --provider deepseek --allow-paid-api --max-api-calls 380 --env-file ../.env --embedding-provider fastembed --embedding-cache-dir ../.build/embedding-models --output-dir evaluation/reports/revalidation
```

每次生成独立 run_id，保留原报告。严格模式不以规则降级计为模型成功；总调用上限可能使运行不完整，须看 complete 与未运行用例。评测订单/工具为模拟，库为独立 SQLite；在线 PostgreSQL/MCP 联测另计。旧25条已被看过，复跑不能继续宣称是当前版本未见过的独立留出集。

## 7. 独立全栈与浏览器复验

只使用独立项目 resolveai-acceptance，端口为 UI3001/API8002/MCP8003/DB5434/Redis6380/Jaeger16687；不用业务卷。需要本地配置 DeepSeek，这部分会真实调用并消耗余额。

准备 `.env.auth`与模型后从根目录：

```powershell
docker compose -p resolveai-acceptance -f docker-compose.yml -f docker-compose.acceptance.yml up -d --build
Set-Location backend
python -m scripts.verify_closeout --restart-checkpoint --docker docker --output-dir ../output/revalidation
$env:VERIFY_API_URL = "http://127.0.0.1:8002/api/v1"
$env:VERIFY_OUTPUT_PATH = "../output/revalidation/async-acceptance.json"
python -m scripts.verify_async_tasks
python -m scripts.benchmark_concurrency --base-url http://127.0.0.1:8002 --output-dir ../output/revalidation/performance
Set-Location ..
npm ci --prefix frontend
$env:PLAYWRIGHT_BROWSERS_PATH = "${PWD}/.build/playwright-browsers"
npm exec --prefix frontend -- playwright install chromium
node scripts/verify-roles.cjs
node scripts/record-demo.cjs
```

浏览器脚本输出至 `output/hardening/roles` 和 `output/hardening/browser`，复跑会刷新这些文件；要保留此前截图录像，先另存这些目录。HTTP/SSE 脚本上面的输出目录则为 output/revalidation，不覆盖历史 closeout。若改过初始密码，验收自动登录不能再使用旧初始凭据，需要使用当前有效测试账号。

等待所有分析、导入、评测、录屏结束后，才能做重启台账核验：

```powershell
Set-Location backend
python -m scripts.verify_durable_metrics --docker docker
Set-Location ..
docker compose -p resolveai-acceptance -f docker-compose.yml -f docker-compose.acceptance.yml stop
```

不要在另一个录屏/长任务未完成时执行重启。停止保留数据。

## 8. 迁移、备份和恢复

启动使用 Alembic 0001/0002/0003/0004，再补通用知识和初始管理员；0003 新增商城和提问记录，并按北京时间重排历史工单显示编号，原编号留存在 legacy_ticket_no，UUID 与对话关联保持不变。0004 为客户会话归属增加可空的客户端删除时间，已有记录默认可见；该标记不影响商户聊天、售后、审批或订单记录。重复启动不重复插通用知识；公开副本不补预置业务工单或商品，默认拒绝破坏性降级。商城扩展的前端验收见 [COMMERCE](COMMERCE.md)。

Windows Docker 升级前 `scripts/backup-database.ps1` 生成私密二进制 pg_dump。一键启动自动执行它；手动 Compose 不会自动执行此宿主机步骤。原生 PostgreSQL 不由该 Docker 脚本备份，需要对其对应实例另行备份。

从根目录可在已启动的、明确命名的 Docker 数据库上演练：

```powershell
python scripts/database-safety.py --container resolveai-acceptance-db-1 --restore-check --output output/revalidation/database-backup.json
```

需要 Python 3.11 和可访问的 docker 命令，也可传 `--docker` 指定 CLI。只恢复到新 UUID 数据库，比较行数/内容摘要，保留备份和副本；不覆盖或删除原库。备份包含数据和认证记录，切勿上传。

## 9. CI、生产预检与边界

CI 配置含锁定依赖的后端测试/审计/免费规则回归、前端检查/构建/全依赖审计、Compose 校验和镜像构建。已有本地同等证据，不代表远端 GitHub Actions 已运行。

公网模板为 docker-compose.public.yml 与 deploy/Caddyfile；需有权控制域名、DNS和服务器，并使用强数据库密码。只更改环境变量不等于更改旧库密码。生产预检、HTTPS条件及人工待办见 [HARDENING](HARDENING.md)。本机 HTTP 不使用 Secure Cookie；公网必须启用。

限流、角色检查、有限测试和依赖审计不等于“零漏洞、零幻觉、商业生产验收完成”。人工语义复核、账单核对、域名/渠道/资金接入授权仍待负责人确认。
