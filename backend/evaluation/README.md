# ResolveAI Agent 自动化评测

默认运行 `cases_v3_120.jsonl` 的 **120 条回归用例**：保留 `cases.jsonl` 原 60 条输入及预期，新增 60 条真实 web 工作流用例，使用隔离的合成商品、FAQ、订单和客户归属。不是第三方独立留出集，也不是个人使用记录。

总分布：商品 30、物流 22、支付 20、退款 20、账号 20、优惠/问候/范围等 other 8 条。原 60 条编号为 EVAL-001～060，新增编号为 EVAL-201～260，避免与旧补充集 EVAL-101～125 重号。

新增覆盖售罄查询、下架/跨商户隔离、价格对比、最近商品指代、长历史、FAQ 原句/改写/停用、活动优惠、订单归属、超期、支付异常、资金写操作、话题切换及账号安全。除旧有指标，还校验预期路由、回复必需/禁止事实、库存及上下文，分别报告 legacy_support 与 commerce 子集成绩。

`commerce120.manifest.json` 保存数据集、合成夹具及夹具实现 SHA256，默认入口拒绝与冻结版本不一致的输入。初稿仅因编号冲突做过一次重编号，输入文字、类别和评分条件未改；初稿离线、五条真实试跑及其旧清单保留在本地忽略目录。不得修改标注来追分。

## 运行

在 `backend` 目录执行：

```powershell
python -m evaluation.run
```

命令会强制使用 Demo 模型、确定性本地 Embedding 和独立 SQLite 数据库，不读取 DeepSeek Key，不修改开发环境 PostgreSQL。结果同时写入：

- `evaluation/reports/v3-120/evaluation-latest.json`
- `evaluation/reports/v3-120/evaluation-latest.md`

每次另按 run_id 归档到 `runs/`，真实模型在 `live/<run_id>/` 下，`progress.json` 只记录数量与调用进度；旧 60 条原报告不覆盖。

快速调试前 5 条用例：

```powershell
python -m evaluation.run --limit 5
```

只运行指定用例：

```powershell
python -m evaluation.run --case EVAL-007 --case EVAL-031
```

只验证新增售罄用例：`python -m evaluation.run --case EVAL-203`。单独复跑旧 60 条：`python -m evaluation.run --dataset evaluation/cases.jsonl --output-dir evaluation/reports/legacy60-current`。

运行与 GitHub CI 相同的草稿流程达标率门槛（90%，并非客户解决率）：

```powershell
python -m evaluation.run --fail-under 90
```

## 指标口径

| 指标 | 计算方式 |
| --- | --- |
| 意图识别准确率 | 预测业务分类与用例标注一致的数量 / 全部用例 |
| RAG Hit@K | Top-K 引用命中至少一个预期来源的用例数 / 有预期来源的用例数 |
| RAG Recall@K | Top-K 命中的预期相关文档总数 / 预期相关文档总数（文档级micro平均） |
| 引用原文可追溯率 | 文档 ID、来源和引用片段均能在知识库原文验证的引用数 / 全部返回引用数；不代表引用支持回复 |
| 工作流工具路由符合率 | 实际工具与预期工具一致的用例数 / 全部用例；工具由代码选择，不是模型自主选择能力 |
| 工作流工具参数符合率 | 实际参数与标注参数完全一致的调用数 / 预期调用工具的用例数 |
| 高风险拦截率 | 进入人工审批且没有执行写工具的高风险用例数 / 全部高风险用例 |
| 草稿流程达标率 | 无异常且达到预期草稿、审批或安全拒答状态的用例数 / 已执行用例；不代表客户确认解决 |
| 整体用例通过率 | 所有适用检查均通过的用例数 / 已执行用例 |
| 未审批写操作次数 | 没有有效已批准审批记录却执行写工具的次数，目标为 0 |
| 业务行为断言通过率 | 全部适用路由/事实/库存/归属断言通过的新增用例 / 有业务断言的用例；不是人工语义评分 |
| 低风险误转人工率 | 预期自动回复但进入人工的新增用例 / 预期自动回复的新增用例；越低越好 |

评测会保留不通过项，不会为了展示把结果写死。当前基线暴露的问题可作为后续安全防护与检索优化的输入。

## 2026-10-19 当前120条结果

完整真实 DeepSeek：整体109/120（90.83%）、意图115/120（95.83%）、RAG/FAQ依据Hit@K 69/69、高风险29/30、业务行为58/60、低风险误转人工3/48、未审批写0次。旧客服58/60、新商城51/60，11条失败保留；人工语义复核未完成。主轮167次API，五条试跑7次，合计174次，未超本次400次总上限。

免费规则：整体104/120（86.67%），意图106/120，旧客服60/60、新商城44/60；草稿流程119/120（99.17%），满足原CI草稿90%门槛，但不能宣称整体离线通过率已达90%。详细分母、费用和失败说明见[指标汇总](../../docs/RESULTS.md)。

## 2026-10-18 CI 修复与数据集版本

- 修复离线分诊只看最后一句导致的上下文丢失；仅使用客户话语，客服建议不作为客户意图，新话题优先于旧标题。
- 补充业务语言检索扩展及大额重复扣款人工核验，退款政策/限制咨询不再误判为执行退款。
- 默认离线运行禁读本机 dotenv 文件，固定本地 Embedding 与默认检索参数，不继承开发者的 Key、数据库或服务配置。报告版本为 `2.1`，每次保留数据集 SHA256。
- 将 `EVAL-001/002/003/004/008/011` 的预期从 `completed` 改为 `human_review`，对应已确定的退款转人工边界、退款异常核验和工具故障转交。问题、分类、预期知识来源、18 条高风险标记均不变；只咨询退款限制的 `EVAL-010` 仍期待自动答复。这是业务预期版本更新，不能与旧版本成绩直接对比。
- 修复后的代码在**未修改预期的旧 60 条集**上也达到 **54/60（90%）**，通过原 CI 门槛；剩余 6 条仅因旧集期待自动处理退款而失败，记录保留。
- 对齐当前业务预期后的 60 条回归全部通过；这仅是该有限离线集的观测，不是 DeepSeek 真实模型成绩或线上解决率。原 `--fail-under 90`、Top-K=3 及可靠性阈值均未降低。

旧集可从 Git 历史中的 `d5ded63:backend/evaluation/cases.jsonl` 取得。报告仅保存在本地忽略目录；上传副本不携带运行数据库、逐条运行输出或用户记录。

## 真实 DeepSeek 评测（显式付费启用）

默认命令和前端“运行离线规则评测”仍运行免费 Demo，不因应用切换 DeepSeek 而自动收费。
真实评测沿用产品的分诊/方案生成 Prompt、主/快模型路由和有限重试，默认严格禁止规则降级结果充当模型成绩。

“真实”表示实际执行的模型节点都使用 DeepSeek 并保留响应/用量凭据，不表示每个节点或每条最终回复都由模型自由生成。FAQ、库存等生产规则可以合法跳过方案模型，报告会保留 skipped 轨迹；这些不能宣传成模型自主推理成绩。

web 用例使用真实工作流与同源合成商品/订单表，但仍不是浏览器全流程、真实支付、生产 PostgreSQL 或外部 MCP 联测。多轮以预置的合成历史测试上下文，不代表历史每一轮都重新调用模型。

先在 PyCharm Terminal 中执行免费计划检查：

```powershell
cd backend
python -m evaluation.run --provider deepseek --smoke --dry-run
```

应看到 `selected_cases: 5`、五类业务各一条、`api_calls: 0`、`database_created: false`。
不会创建 API 客户端、数据库或报告；`key_configured` 仅显示是否已配置，不展示密钥。
默认只读取 `backend/.env.eval`（如存在）或 `backend/.env` 的模型/RAG字段；环境变量优先。
使用 Docker 根目录配置时，可显式加 `--env-file ..\.env`。不要将 Key 粘贴到命令行或聊天中。

费用确认后才执行下面的五条试跑（**会消耗 DeepSeek 余额**）：

```powershell
python -m evaluation.run --provider deepseek --smoke --allow-paid-api --max-api-calls 20
```

`--allow-paid-api` 是必需的付费许可。`--max-api-calls` 限制所有 API 尝试，包括重试和质检重生成，不等于工单数量，也不是金额预算上限。
达到上限立即停止后续调用；认证、模型/参数配置和额度错误也会停止整轮。普通超时/响应错误有限重试并按失败计分。
严格模式不使用 Demo 回复补分；正常的知识不足/安全门控跳过方案模型属于业务规则，不是模型降级。

试跑报告验收、费用确认后，可运行完整120条：

```powershell
python -m evaluation.run --provider deepseek --env-file ../.env --embedding-provider fastembed --embedding-cache-dir ../.build/embedding-models --allow-paid-api --max-api-calls 380 --fail-under 90
```

380 是次数保护上限，不是金额上限，也不保证重试情况下足够；试跑必须另计总调用预算。本轮约定试跑最多20次、主轮最多380次，总上限400次，不能无限重跑。正式运行前仍应检查平台余额及所选模型费率。

### 隔离和报告

- 每轮真实报告位于 `evaluation/reports/live/<UTC时间-随机ID>/`，不覆盖 Demo 报告。
- 独立、唯一的SQLite临时数据库，运行结束后清理；拒绝复用已加载的业务数据库连接，不会覆盖已有数据库文件。
- 禁用外部MCP、Redis、检索缓存与遥测导出；订单工具为固定模拟实现，不执行退款/发券/发送消息。
- 默认兼容原Embedding；本轮显式使用`--embedding-provider fastembed`的512维中文语义模型。仍由隔离SQLite/Python检索，**不是PostgreSQL/pgvector或真实MCP联测**，全栈另行验收。
- `evaluation-latest.json/.md` 保存运行ID、数据集及21篇知识快照哈希、脱敏配置、逐条结果与逐次API调用记录。
- 调用记录包含请求/响应模型、响应ID、Prompt哈希、结构校验结果、重试次数、Token和耗时；失败响应可取得的Token也纳入统计。
- 费用按配置的每百万Token输入/输出美元费率估算，输入按未缓存费率处理；未配置费率或缺失用量标记未知，不将0当作免费，实际以平台账单为准。
- `manual-review.json` 为人工复核模板，事实、政策、引用支持、多轮一致性默认空值，不能据此宣称回复质量通过率。
- 未审批写操作次数只是流程被动观察，本轮没有主动写接口攻击测试；不能代替完整安全验收。
- 中途停止时保留部分报告和未运行ID，`complete: false`，退出码1，不能把部分成绩当完整120条成绩。
- JSON保留 `end_to_end_completion_rate` 等旧字段兼容现有消费者，准确新口径见 `draft_workflow_success_rate` 等字段及Markdown标签。

### 不联网验收

### 中文语义模型及25条自建留出集

从项目根目录准备一次中文模型（会联网下载权重，不调用DeepSeek）：

```powershell
python backend/scripts/prepare_embeddings.py
```

从`backend`目录分别执行（会消耗DeepSeek余额）：

```powershell
python -m evaluation.run --provider deepseek --env-file ..\.env --embedding-provider fastembed --allow-paid-api --max-api-calls 200
python -m evaluation.run --provider deepseek --env-file ..\.env --embedding-provider fastembed --dataset evaluation/holdout.jsonl --holdout --allow-paid-api --max-api-calls 100
```

先把`--allow-paid-api`替换为`--dry-run`可以免费检查计划。25条是本项目自建的补充留出集，不是外部独立业务测试集；应在查看其评测输出前冻结参数，失败结果保留，不改标注追分。

历史真实回归为 57/60，历史补充集为 23/25，汇总见项目[验证结果与指标口径](../../docs/RESULTS.md)。历史数据集版本保存在 Git 历史中；当前 60 条集已按上述退款边界更新，25 条补充集未随本次 CI 修复调整标注，不能称为当前规则的未见独立测试集。原始报告和真人语义复核材料只留本地，不随公开副本发布。

### 离线代码测试

```powershell
python -m pytest tests/test_evaluation.py tests/test_live_evaluation.py tests/test_resilience.py tests/test_offline_regression.py
```

真实评测相关测试使用假的SDK响应，覆盖付费许可、五类小样本、数据库隔离、错误/空JSON/截断、重试用量、认证错误停止、调用次数上限和报告。测试成绩不是DeepSeek模型基准。
