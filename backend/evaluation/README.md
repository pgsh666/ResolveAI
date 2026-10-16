# ResolveAI Agent 自动化评测

评测集包含 60 条可版本化 JSONL 用例，退款、物流、支付、商品、账号各 12 条，并覆盖普通咨询、多轮对话、信息缺失、高风险操作、越权请求、Prompt 注入、工具超时和知识库无答案。

## 运行

在 `backend` 目录执行：

```powershell
python -m evaluation.run
```

命令会强制使用 Demo 模型、确定性本地 Embedding 和独立 SQLite 数据库，不读取 DeepSeek Key，不修改开发环境 PostgreSQL。结果同时写入：

- `evaluation/reports/evaluation-latest.json`
- `evaluation/reports/evaluation-latest.md`

快速调试前 5 条用例：

```powershell
python -m evaluation.run --limit 5
```

只运行指定用例：

```powershell
python -m evaluation.run --case EVAL-007 --case EVAL-031
```

为 CI 设置端到端完成率门槛（百分数）：

```powershell
python -m evaluation.run --fail-under 80
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

评测会保留不通过项，不会为了展示把结果写死。当前基线暴露的问题可作为后续安全防护与检索优化的输入。

## 真实 DeepSeek 评测（显式付费启用）

默认命令和前端“运行完整评测”仍运行免费 Demo，不因应用切换 DeepSeek 而自动收费。
真实评测沿用产品的分诊/方案生成 Prompt、主/快模型路由和有限重试，默认严格禁止规则降级结果充当模型成绩。

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

试跑报告验收、费用确认后，可运行完整60条：

```powershell
python -m evaluation.run --provider deepseek --allow-paid-api --max-api-calls 200 --fail-under 90
```

200是次数保护上限，不保证一定足够。仅API调用上限不承诺实际费用，正式运行前仍应在DeepSeek平台检查余额及所选模型费率。

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
- 中途停止时保留部分报告和未运行ID，`complete: false`，退出码1，不能把部分成绩当完整60条成绩。
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

本轮回归报告：`reports/live/20261012T055038Z-6318f55b`（57/60）；留出报告：`reports/live/20261012T065419Z-38ebb8fd`（23/25）。原基线及原60条JSONL保留。真人语义复核仍待完成，AI辅助观察见项目`output/closeout/AI-assisted-review.md`。

### 离线代码测试

```powershell
python -m pytest tests/test_evaluation.py tests/test_live_evaluation.py tests/test_resilience.py
```

真实评测相关测试使用假的SDK响应，覆盖付费许可、五类小样本、数据库隔离、错误/空JSON/截断、重试用量、认证错误停止、调用次数上限和报告。测试成绩不是DeepSeek模型基准。
