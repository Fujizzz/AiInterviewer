# Evaluation 证据提取与评分开发计划

## 文档目的

本文档用于指导 AI Interviewer 的正式 Evaluation 模块开发，重点是建立可追溯的 Evidence Extractor、行为锚定 Rubric 评分和确定性分数聚合。本计划以仓库提交 `3f8ad6a` 为基线，供后续开发、评审和任务拆分时持续读取。

核心结论是：评分链路应采用“LLM 提取证据 + LLM 匹配行为锚点 + 程序确定性聚合”的分层架构。模型不直接生成 competency 总分或 overall score；每个可发布分数都必须能反查到候选人回答原文、证据项、rubric criterion、行为锚点和聚合公式。

## 当前基线

当前仓库已具备以下能力：

- `agents/planning/` 已实现基于时间预算的 Interview Planner，每个 topic 包含 `objective`、`completion_criteria`、时间预算和进度状态。
- `app/adapters/evaluation.py` 已有临时 LLM Evaluation Adapter，能提取对话分析和多能力证据。
- 证据 quote 必须是当前回答的原文子串。
- 同一回答不允许重复返回同一 competency。
- Evaluation 超时、模型失败或证据校验失败时，会保留回答但不产生分数或虚构证据。
- `agents/evidence.py` 会将当前 `DimensionEvidence` 聚合到 `CompetencyState`。
- `app/reporting/final_report.py` 使用岗位权重计算 overall score，LLM 只负责报告文案。
- 第一阶段已完成（2026-10-03）：`evaluation/contracts.py` 提供内部 schema `1.0`；
  `evaluation/rubric.py` 提供 loader、schema 和 assessment 引用校验；
  `evaluation/rubrics/1.0.0/` 包含六项能力、21 个 criterion、105 个行为锚点。
- `tests/evaluation/` 已覆盖新契约、Rubric 和 Evidence → CriterionAssessment →
  ScoreSnapshot 示例；正式模块尚未接入运行链路，当前分数仍由原 adapter 产生。
- 第二阶段已完成（2026-10-03）：`evaluation/analyzer.py`、`extractor.py` 和
  `service.py` 提供独立 schema/Prompt、Planner 目标输入、原子多片段提取、确定性 ID
  及阶段化 fail-closed 恢复；本阶段为可单独调用的内部服务，线上接入仍在第五阶段。
- 第三阶段已完成（2026-10-03）：`validator.py`、`resolution.py`、`resolver.py` 和
  `eligibility.py` 提供完整来源校验、claim 比较键、六类关系、独立组、撤回与矛盾状态，
  并按 criterion 限制同组贡献。`evaluate_resolved()` 可编排前三阶段；尚不生成分数。
- 第四阶段已完成（2026-10-03）：`judge.py`、`policy.py`、`aggregation.py` 和
  `aggregator.py` 提供独立 Rubric Judge、版本化质量权重、三级聚合、coverage/reliability、
  显式发布门槛与完整重放记录。`evaluate_scored()` 可编排前四阶段；未接入线上评分。

与目标架构相比，仍存在以下缺口：

- 正式 `evaluation/` 已有契约、Rubric、Analyzer、Extractor、Resolver、Judge 和
  Aggregator；运行链路接入、shadow mode、持久化与标注校准仍待后续阶段实现。
- 当前线上旧 adapter 仍由一次模型调用负责对话分析、证据提取、competency 识别和
  rubric level 判定；第二阶段内部服务已拆分前两者，等待后续评分模块与第五阶段接入。
- `DimensionEvidence` 缺少原文 span、criterion、rubric 版本、独立性和纳入或排除原因。
- Agent 核心仍在重新计算 score 和 coverage，与“Evaluation 负责评分”的边界不一致。
- 线上旧评分的简单平均仍没有区分 supported、weak、duplicate 和 disputed evidence。
- 线上旧评分中未覆盖 competency 可能在 overall score 的权重重新归一化中被忽略；
  第四阶段内部评分已保留完整重要性分母，并要求覆盖、mandatory 和 anchor 门槛。
- 尚无 append-only evidence ledger；第四阶段已提供可重放 score snapshot 配套记录，
  第五阶段需持久化完整 AggregationRecord，而不是仅保存分数。

## 模块边界

Evaluation 模块负责：

- conversation analysis 的结构化产出；
- evidence extraction 和 grounding validation；
- evidence 去重、独立性和关系解析；
- rubric matching 和 criterion assessment；
- score、coverage、reliability 和 overall score 发布门槛；
- evidence ledger 和 score snapshot；
- 面向报告的可解释评分数据。

Agent 核心负责：

- 消费安全的 conversation feedback；
- 根据回答状态调整追问、话题和难度；
- 使用 `thread_complete`、missing information 和 contradiction 驱动 Planner；
- 通过 Repository 原子提交新状态；
- 不自行重新计算分数。

Question Agent 不应读取完整 rubric、当前分数或候选人排名。Evaluation 只向它暴露安全的 `missing_information`、`thread_complete`和已落地的矛盾摘要。

## 目标数据流

```text
Question + Candidate Answer + relevant thread history
                         |
                         v
              Conversation Analyzer
                         |
                         v
              Evidence Extractor Agent
                         |
                         v
            Evidence Validator and Resolver
                         |
                         v
                 Rubric Judge Agent
                         |
                         v
          Deterministic Score Aggregator
                         |
                         v
      Evidence Ledger + Score Snapshot + Report
```

Conversation Analyzer 和 Evidence Extractor 可以使用同一模型供应商，但必须有独立的 schema、Prompt 和错误边界。Rubric Judge 只读取已通过程序校验的 evidence，Score Aggregator 必须是可确定性重放的纯程序逻辑。

## 核心数据契约

### EvidenceItem

`EvidenceItem` 只表达一个可独立判断的候选人陈述，至少包含：

```text
evidence_id
answer_id
question_id
thread_id
project_id
quote_spans[]
normalized_claim
evidence_kind
ownership_scope
factuality
specificity
related_evidence_ids[]
relation
extraction_version
```

`quote_spans` 必须保存 exact quote、`char_start` 和 `char_end`。一个事实可以引用多个不连续片段，不应被限制为当前的单一 `quote`。

### EvidenceRelation

支持以下关系：

- `new`；
- `duplicate`；
- `refines`；
- `supports`；
- `contradicts`；
- `retracts`。

不能按“同一 thread + 同一 competency”直接覆盖。同一项目事件或技术决策的追问补充应当合并到同一 independence group，而不是重复累加 evidence count。

### CriterionAssessment

`CriterionAssessment` 至少包含：

```text
competency
criterion_id
rubric_version
evidence_ids[]
assigned_level
matched_anchor_ids[]
decision
reason_codes[]
concise_rationale
counter_evidence_ids[]
```

`decision` 建议限定为 `included`、`insufficient`、`excluded` 或 `disputed`。`concise_rationale` 是可审计的结构化摘要，不保存或要求模型的隐藏思维链。

### ScoreSnapshot

`ScoreSnapshot` 至少包含：

- evaluation schema 和 rubric 版本；
- 每个 criterion 的有效 evidence 及权重；
- competency score、coverage 和 reliability；
- overall coverage；
- overall score 是否可发布；
- 未发布的 reason codes；
- 聚合策略版本；
- `supersedes_snapshot_id` 和重评原因。

重新评分必须创建新 snapshot，不得覆盖历史分数。

## Evidence Extractor 机制

Evidence Extractor 只接收：

- 当前不可变 Question 快照；
- 当前 CandidateAnswer；
- 当前 thread 的有限历史回答；
- competency taxonomy；
- 已存在 evidence 的精简索引，用于判断重复、补充或矛盾。

Extractor 不应读取候选人当前分数、岗位权重、最终录用判断或排名。简历 claim 只是待核实线索，不能直接计分。

模型输出后必须执行程序硬校验：

- quote 必须是当前回答的精确子串；
- span 与 quote 必须一致；
- quote 不得来自问题、简历或历史回答；
- `label_only`、yes/ok、拒答不能形成可评分证据；
- 每条 evidence 必须有明确 claim；
- 校验失败时 fail closed，不尝试将虚构内容修正成可计分证据。

Evidence ID 应根据 `answer_id + quote spans + normalized claim + evidence kind` 确定性生成，保证同一请求重放时结果稳定。

## Evidence 质量与独立性

不再仅使用单一 `strength: 0..1`。Evidence 质量应拆成可展示的分量：

- grounding；
- relevance；
- directness；
- specificity；
- outcome support；
- consistency；
- independence。

每个分量使用枚举值和 reason code，例如 `personal_action`、`team_only`、`concrete_mechanism`、`measured_result`、`unresolved_contradiction`。内部可以通过版本化的映射转换为权重，但报告必须保留原始分量和原因。

同一经历在多次追问中重复表述，不能被视为多个独立证据。不同问题也不自动表示证据独立。

## Rubric 设计

为以下六个 competency 建立独立行为锚定 rubric：

- `technical_depth`；
- `ownership`；
- `decision_making`；
- `debugging`；
- `evaluation`；
- `adaptability`。

每个 competency 必须拆成多个 criterion。例如 debugging 可以包含：

- 问题界定；
- 假设构建；
- 诊断方法；
- 根因验证；
- 修复验证；
- 预防与复盘。

每个 criterion 定义专属的 1–5 级行为锚点。不应继续使用同一套“1=识别概念，5=深度洞察”的通用描述来评估所有能力。

岗位 seniority 可以选择不同 rubric pack，但岗位权重只参与最后聚合，不传给 Evidence Extractor 或 Rubric Judge。

## 确定性聚合策略

每个 criterion assessment 的有效权重由版本化策略计算：

```text
effective_weight =
    grounding_gate
  * relevance_gate
  * directness_weight
  * specificity_weight
  * consistency_weight
  * independence_weight
```

grounding 或 relevance 不通过时权重为 0。同一 independence group 只保留最高质量证据的完整贡献，其他补充只用于丰富原 evidence，不重复刷分。

Criterion score 的初始公式为：

```text
criterion_score =
sum(level * effective_weight) / sum(effective_weight)
```

但只有在独立证据数、有效权重和矛盾状态达到门槛时才发布分数，否则返回 `score = null`。具体门槛需通过人工标注集校准，不在首版直接写死。

Competency score 和 coverage 分开计算：

```text
competency_score =
sum(criterion_score * criterion_weight)
/ sum(covered criterion weight)

competency_coverage =
sum(scoreable criterion weight)
/ sum(all required criterion weight)
```

缺失证据不直接按 0 分处罚，但 coverage 不足时不允许发布看似完整的 competency score。

Overall score 只在以下条件成立时发布：

- 岗位重要性覆盖率达标；
- mandatory competency 都满足可评分门槛；
- 必要 anchor assessment 已完成；
- 不存在阻断性的未解决矛盾或 Evaluation failure。

不满足时返回：

```text
overall_score = null
status = insufficient_evidence
reason_codes = [...]
```

问题 difficulty 不直接作为加分乘数。高难度问题只有在回答内容真正匹配更高 rubric anchor 时才会提高分数。

## Planner 集成要求

`thread_complete` 会影响 topic 状态和动态 replan，因此它必须和评分证据解耦：

- Conversation Analyzer 需要读取当前 agenda 的 `objective` 和 `completion_criteria`；
- `thread_complete=true` 只表示当前 topic 的对话目标已满足；
- `thread_complete` 不表示 competency 已达到评分覆盖门槛；
- Evaluation 失败、无法解析或证据不足时不得将 topic 标记为 completed；
- Planner 只接收 conversation-safe feedback，不接收分数或完整 rubric。

为了保证跨候选人可比性，后续还需要在 Planner 之上定义隐藏的 `AssessmentBlueprint`。它指定 mandatory competency 和 anchor information goal，但只向出题侧提供可观察目标，不暴露分数或详细评分规则。

## 开发阶段

### 阶段一 契约和 Rubric

状态：**已完成（2026-10-03）**。

交付内容：

1. 新增 `evaluation/contracts.py`。
2. 定义 `EvidenceItem`、`QuoteSpan`、`EvidenceRelation`、`CriterionAssessment`、`CompetencyScore`、`ScoreSnapshot` 和 `EvaluationResult`。
3. 建立六个 competency 的版本化 rubric 文件。
4. 实现 rubric loader 和 schema validation。
5. 添加契约、rubric 及示例 fixture 测试。

该阶段不替换现有评分流程，不修改线上分数。

实际交付与验收：

- 契约包含本阶段要求的全部七类模型/枚举，并增加 criterion 贡献记录、
  空分数发布状态、失败结果约束及 snapshot 重评关联字段。
- QuoteSpan 保留 Python Unicode 字符偏移和原始空白；支持不连续片段，
  可显式针对 CandidateAnswer 校验回答身份和原文，避免仅凭长度判定 grounding。
- Rubric 文件按 `evaluation/rubrics/1.0.0/` 保存；debugging 包含六项 criterion，
  其他五项能力各包含三项 criterion。每项均有专属的 1–5 级行为描述。
- Loader 拒绝缺失能力、混合版本、文件名与能力不符、重复 YAML key、缺失/重复等级、
  非法权重和多余字段；assessment 校验拒绝跨能力、跨 criterion 或等级不匹配的引用。
- 示例位于 `tests/evaluation/fixtures/evidence_to_assessment.json`，包含多片段证据、
  included/excluded assessment、criterion 贡献和 overall score 为 null 的 snapshot。
- `evaluation/README.md` 记录公开使用方式、内部与共享契约边界、版本迁移方式及未实现范围。
- 验收：新增 132 项测试通过；全仓 `.venv/bin/pytest -q` 为 **350 passed，5 subtests passed**；
  `.venv/bin/ruff check .` 通过。回归包含 Evaluation recovery、dialogue evaluation 和
  interview planning。现有 Adapter、Question Agent、Planner、报告和数据库未修改。

版本与未决策项：

- 内部 evaluation schema `1.0`、Rubric `1.0.0` 与共享 Agent contract `2.0` 分开版本化；
  无线上数据迁移。后续变更新增 Rubric 版本目录，重评生成新 snapshot，不覆盖历史版本。
- 首版各 criterion 使用等权 `1.0`，行为锚点待人工标注校准；未设发布阈值。
  示例中的 `fixture-only-1` 仅标识人工构造的测试数据，不代表聚合策略已实现。
- reason code 词表、质量分量到权重的映射、seniority pack、独立证据与覆盖率门槛、
  reliability 算法和岗位权重 trace 的最终结构留待后续阶段确定。
- 第一阶段完成时仅验证结构、原文对齐和 Rubric 引用；当时语义非回答过滤、真实历史证据关系、
  去重与独立性、矛盾消解、确定性 ID、分数数学重放及 append-only 持久化尚未实现。

### 阶段二 Conversation Analyzer 和 Evidence Extractor

状态：**已完成（2026-10-03）**。

交付内容：

1. 将对话控制分析与评分证据提取拆分为独立 schema 和 Prompt。
2. 实现原子 evidence 拆分和多 quote span 输出。
3. 传入当前 Planner objective 和 completion criteria。
4. 实现确定性 evidence ID。
5. 保持现有 fail-closed Evaluation recovery 行为。

实际交付与验收：

- `inputs.py` 将当前 Question/Answer、Planner objective/completion_criteria 冻结为快照；
  仅保留最近 10 条当前 thread 回答和最多 50 条历史 evidence 精简索引。模型输入按字段
  白名单构造，不提供岗位权重、当前分数、简历或录用判断。
- `analyzer.py` 与 `extractor.py` 使用独立 Pydantic schema、版本化 Prompt、模型调用
  和错误边界。Analyzer 读取当前 Planner 目标；Extractor 按独立 claim 提取，可返回同一
  能力下的多个 claim，每个 claim 支持多个有序且不重叠的 quote span，不产生 rubric level。
- `ids.py` 使用规范 UTF-8 JSON + SHA-256，根据 answer_id、原始 spans、normalized_claim
  和 evidence_kind 生成 identity v1；不受 request_id 或模型输出列表顺序影响。
- 复用第一阶段精确原文/offset 校验，拒绝问题、简历和历史回答中的非当前原文；错误 quote
  不修补，同批任一项不合法或 ID 重复时整体失败。常见非回答和裸标签有程序门禁，
  其余语义非回答由独立 Analyzer 分类；未完成/缺失 Planner 条件、无证据及失败不完成 topic。
- `service.py` 顺序编排两个阶段，支持单独设置模型与超时。失败结果有明确阶段 reason code，
  无 evidence/assessment/snapshot；取消继续传播，迟到结果不发布，不修改原始回答。
- 原有 Adapter、Question Agent、Planner、报告和数据库未修改。失败安全分析通过既有
  feedback 提交的集成测试验证回答保留、topic 不完成、继续追问和反馈幂等。
- 验收：本阶段新增 **95 项测试**，`tests/evaluation/` 共 **227 项**；全仓
  `.venv/bin/pytest -q` 为 **445 passed，5 subtests passed**；
  `.venv/bin/ruff check .` 与 `git diff --check` 通过。包含真实异步超时、取消和迟到结果
  测试，以及原 Evaluation recovery、dialogue evaluation、interview planning 回归。
  模型输出使用测试替身，未调用在线模型；语义提取质量和标注校准仍待后续阶段验证。

版本与后续边界：

- 内部 schema `1.0`、Rubric `1.0.0` 和共享 Agent contract `2.0` 不变；新增 Analyzer
  `analyzer-1.0.0`、Extractor `extractor-1.0.0` 和 evidence identity v1，无线上数据迁移。
- ID 稳定性针对同一提取内容的重放，不保证再次调用模型生成同样的 claim。原子性和
  claim 忠实度主要由 Prompt 约束，需要后续人工标注评估。
- 第二阶段输出 `relation=new` 是等待 Resolver 的初始状态，不代表独立新证据；精简历史索引
  不直接决定去重、关系或计分。第三阶段现已实现独立入口的 group、关系与矛盾消解。
- Analyzer 保留双候选人原文的对话矛盾检查；该检查不替代后续 Evidence Resolver。
  第二阶段完成时尚未实现正式 EvaluationPort、shadow mode、评分或持久化；
  第四阶段现已提供内部评分，正式端口、shadow mode 与持久化仍按第五阶段推进。

### 阶段三 校验、去重和矛盾

状态：**已完成（2026-10-03）**。

交付内容：

1. 实现 quote/span 硬校验。
2. 实现 normalized claim 和 independence group。
3. 识别 duplicate、refines、supports、contradicts 和 retracts。
4. 未解决矛盾只降低相关 criterion 的可评分性，不自动把候选人评为 0 分。
5. 限制同一 independence group 的重复贡献。

实际交付与验收：

- `validator.py` 统一提取与解析的硬门禁；重新验证原始模型、回答/问题/thread/project/
  interview 身份、exact quote/span、非回答与空 claim，包含历史证据原文校验。
  不接受只含 claim 的精简索引作为关系依据，不修补虚构 quote，不允许多段非回答拼成证据。
- `resolution.py` 保存原始来源、关系提议、解析历史、证据状态和冲突记录。`resolver.py`
  通过独立 Prompt/schema 提议 `new/duplicate/refines/supports/contradicts/retracts`，
  程序拒绝未知/未来/自引用、身份冲突、跨项目合并、遗漏或重复决策和无效撤回原文。
- claim 比较键只做 NFC 与空白规范化，保留数字、否定、大小写与标点，不改原文或 v1 ID。
  同一事件的规范化重复 claim 会被程序强制去重；同一事件的新事实与追问补充共用独立组，
  支持跨问题/thread 关联。不同经历保留独立组，无法确认独立性的组暂不贡献。
- `replay_resolution()` 从完整原始来源和关系提议重建派生视图，不调用模型、不修改历史。
  group ID 由 interview 与组内最早 evidence ID 确定。未解决矛盾保留双方原文引用，
  duplicate/refines 不能绕过矛盾或撤回门禁；撤回仅消解相关冲突，冲突记录仍保留。
- `eligibility.py` 只阻断引用受影响陈述的 criterion assessment；同组无关事实继续可用。
  disputed/insufficient/excluded 的 level 为 null，不制造 0 分。每个 criterion、每组只
  选择一个完整贡献，按 specificity、ownership、factuality 和稳定 ID 排序，补充片段保留
  为审计引用，不增加独立证据数；选择不依据等级高低，未实现数值权重和分数聚合。
- `EvaluationService.evaluate_resolved()` 顺序编排三个阶段，支持独立 Resolver 模型和期限。
  解析失败丢弃本次 evidence 与 completion，保留回答及旧历史；取消传播、迟到结果不发布。
  原 `evaluate()` 保持提取入口，旧线上 Adapter、Question Agent、Planner、报告和数据库未改。
- 本阶段新增 **93 项测试**，`tests/evaluation/` 共 **320 项**；全仓 `.venv/bin/pytest -q`
  为 **575 passed，5 subtests passed**，`.venv/bin/ruff check .` 与 `git diff --check` 通过。
  测试覆盖关系与原文、重复贡献、局部矛盾、撤回、确定性重放、真实异步超时
  和取消；扩展现有反馈提交回归验证 Resolver 失败后的回答保留、topic 不完成、继续追问和幂等。

版本与后续边界：

- 新增 Resolver/历史契约 `resolver-1.0.0`、claim 规范化 `claim-nfc-whitespace-1`、
  贡献选择 `episode-selection-1.0.0`。内部 schema `1.0`、Rubric `1.0.0`、evidence identity v1
  和共享 Agent contract `2.0` 不变，无线上数据迁移。规则变更需新增版本并重评，不能覆盖历史。
- 相同来源与关系提议可以确定性重放；模型再次生成不保证相同语义判断。关系识别、事件独立性、
  原子 claim 忠实度和显式撤回语义仍需人工标注校准；本阶段仅使用模型替身，未调用在线模型。
- 完整来源由调用方按时间顺序提供，程序不静默截断；持久化完整性、append-only 与 CAS 在第五阶段。
  撤回后的旧 assessment 需重新 Judge，不能恢复失效等级。质量分量、effective weights、发布阈值、
  reliability、分数/snapshot 和数学重放在第四阶段实现；本阶段的贡献排序不代表已校准评分策略。

### 阶段四 Rubric Judge 和 Aggregator

状态：**已完成（2026-10-03）**。

交付内容：

1. Rubric Judge 将已验证 evidence 映射到 criterion 和行为锚点。
2. 实现版本化 effective weight 策略。
3. 实现 criterion、competency 和 overall 的纯程序聚合。
4. 实现 coverage、reliability 和 score publish gate。
5. 输出完整 aggregation trace，支持确定性重放。

实际交付与验收：

- `judge.py` 与独立 Prompt/schema 将已验证 evidence 映射到 criterion/anchor；每项
  criterion 必须显式评估，缺口为 insufficient。每个 criterion/episode 最多一个等级，
  独立经历分别评估；全部来源必须被引用或记录未映射原因。拒绝未知证据、漏评、跨组等级、
  重复评估、非法锚点和 level。Judge 输入不含岗位权重、发布参数、当前分数或问题 difficulty。
- Judge 与 Aggregator 均从完整原文历史重放校验；`RubricJudgement` 绑定历史与 Rubric
  内容摘要，程序生成稳定 assessment ID。撤回、关系改变、原文或 Rubric 改变后不能复用旧等级。
  模型输出顺序不改变 ID；语义重新生成仍不保证一致。
- `policy.py` 记录 grounding、relevance、directness、specificity、outcome support、
  consistency、independence 的枚举标签和 reason codes，提供版本化 effective weight。
  同一 criterion/group 只取最大有效质量的完整贡献，其余保留补充引用；选择不依据等级。
  duplicate/disputed/retracted/independence-unresolved 贡献为 0；无效原文整体失败。
- `aggregator.py` 使用固定 Decimal 上下文计算 criterion、competency 和 overall。
  criterion 采用证据加权均值；competency 按 Rubric criterion weight 聚合，required
  criterion coverage 单独计算；overall coverage 保留岗位全部重要性权重，不忽略未覆盖项。
  reliability 由平均质量、独立证据支持度和等级一致性构成，明确标为待校准启发式指标。
- `PublicationThresholds` 七个字段必须由调用方显式提供，无内置生产发布阈值。独立数、
  有效权重、reliability、coverage、mandatory competency、必要锚点评估、未解决矛盾
  和 Evaluation failure 都有可审计门禁。未达标为 null，不把缺证或矛盾评为 0 分。
  未解决矛盾只阻断相关 criterion 及 overall，无关 criterion 仍可评分。
- `AggregationRecord` 保存原始来源/关系、Rubric 全文、Judge 和门禁后评估、策略配置、
  岗位权重、所有贡献因子、选择/排除原因、三级分子分母与 reliability 分量。
  `replay_aggregation()` 无模型或文件读取，重新校验并计算整个记录，拒绝不一致 trace。
  snapshot ID 根据完整规范化输入确定；重评必须同时提供前序 ID 和原因，生成新记录。
- `EvaluationService.evaluate_scored()` 编排四阶段；Judge 模型与期限可独立配置。
  任一阶段失败无本次 evidence、assessment、resolution 或 snapshot，topic 不完成；
  取消传播，迟到结果不发布。调用成功但 score coverage 不足，不会否定已满足的对话目标。
  `evaluate()`、`evaluate_resolved()` 保持兼容；旧 Adapter、Planner、报告和数据库未修改。
- 本阶段新增 **98 项测试**，`tests/evaluation/` 共 **418 项**；全仓 `.venv/bin/pytest -q`
  为 **673 passed，5 subtests passed**。`.venv/bin/ruff check .` 与 `git diff --check`
  通过。覆盖权重/三级数学、发布边界、重复/补充/撤回/局部矛盾、完整 JSON 重放、篡改拒绝、
  独立超时/取消及迟到结果；扩展反馈提交回归验证 Judge/聚合失败后的回答保留、继续追问和幂等。

版本与后续边界：

- 新增 `judge-1.0.0`、`aggregation-1.0.0`、`aggregation-trace-1.0.0` 和
  assessment/snapshot identity v1。Evaluation schema `1.0`、Rubric `1.0.0`、
  Resolver `resolver-1.0.0` 和共享 Agent contract `2.0` 不变，无线上数据迁移。
  数值贡献选择独立版本化，不原地修改第三阶段 `episode-selection-1.0.0` 的公开序规则。
- 发布配置和岗位配置全文进入 snapshot 输入摘要；同名配置的参数变化也产生不同 ID。
  改变映射/公式必须升级策略版本，并保留旧算法供历史重放；重评不覆盖旧记录。
- 当前权重、reliability 和 Rubric 仍待人工标注校准；测试配置只验证逻辑，不是生产默认值。
  本阶段仅使用模型替身，未调用在线模型，不能据此宣称语义判断或招聘效度已经验证。
- 单独 ScoreSnapshot 不足以重放，调用方须保存配套完整 AggregationRecord。
  原始历史完整性、持久化、append-only、CAS、历史失败状态维护和线上 shadow mode 在第五阶段；
  最终报告视图、人工标注集和正式阈值校准在第六阶段。当前不改变用户可见的线上分数。

### 阶段五 集成和持久化

交付内容：

1. 实现正式 `EvaluationPort`。
2. 保留当前 adapter 作为过渡兼容层。
3. 使用 shadow mode 同时运行新旧评分，新结果暂不影响用户界面。
4. 将 score 和 coverage 聚合从 `agents/evidence.py` 迁移到 Evaluation。
5. Repository 保存 append-only evidence ledger、criterion assessments 和 score snapshots。
6. 使用 `base_state_version` 和 CAS 防止基于过期证据计算分数。

### 阶段六 报告和校准

交付内容：

1. 更新最终报告，展示 competency score、coverage、reliability 和 insufficient evidence。
2. 为每个分数展示 exact quote、criterion、anchor、贡献权重和排除原因。
3. 区分内部完整评估视图和候选人安全视图。
4. 建立人工标注数据集并校准证据门槛。
5. 评估 evidence precision/recall、rubric level agreement、weighted kappa、重放稳定性和分组偏差。

## 建议文件结构

```text
evaluation/
|-- contracts.py
|-- service.py
|-- analyzer.py
|-- extractor.py
|-- validator.py
|-- resolver.py
|-- ids.py
|-- judge.py
|-- aggregator.py
|-- policy.py
|-- explanation.py
|-- rubrics/
|   |-- technical_depth.yaml
|   |-- ownership.yaml
|   |-- decision_making.yaml
|   |-- debugging.yaml
|   |-- evaluation.yaml
|   `-- adaptability.yaml
`-- tests/
    |-- fixtures/
    |-- test_contracts.py
    |-- test_extractor.py
    |-- test_validator.py
    |-- test_resolver.py
    |-- test_judge.py
    |-- test_aggregator.py
    `-- test_replay.py
```

实际测试位置应与仓库统一的 `tests/` 结构对齐；上述结构中的 `evaluation/tests/` 只表示逻辑分组，落地前需根据现有测试约定确定最终路径。

## 第一个开发 PR

第一个 PR 只包含：

1. `evaluation/contracts.py`；
2. 六个 competency rubric 文件；
3. rubric loader 和 schema validation；
4. 契约与 rubric 单元测试；
5. Evidence 到 CriterionAssessment 的示例 fixture；
6. Evaluation 模块 README 和对外边界说明。

第一个 PR 不包含：

- 不替换现有 Evaluation Adapter；
- 不修改 Question Agent Prompt；
- 不修改 Interview Planner；
- 不改变线上分数；
- 不修改最终报告；
- 不做数据库迁移；
- 不引入新的 Agent 框架。

## 测试要求

单元测试至少覆盖：

- quote 和 span 精确对齐；
- 问题文本、简历 claim 不能成为回答证据；
- yes/ok、label-only、拒答和 explicit unknown 不计分；
- duplicate evidence 不提高 evidence count 或 score；
- 同一经历的追问补充被合并；
- 不同独立经历可分别贡献；
- 矛盾需要两个真实的候选人原文片段；
- unresolved contradiction 使相关证据 disputed，但不制造 0 分；
- 同一请求重放产生相同 evidence ID 和 score snapshot；
- 模型超时、无效 JSON 和非法 quote 时 fail closed；
- Evaluation 失败时保留回答且不完成 topic；
- coverage 不足时 overall score 为 `null`；
- 已发布分数可从 snapshot 确定性重建。

集成回归需继续覆盖：

- Evaluation failure 后 Agent 可继续询问；
- Planner 不会因评分失败错误关闭 topic；
- feedback request 幂等；
- Repository CAS 和原子提交；
- shadow result 不影响用户当前结果；
- 新旧合同在过渡期内能够兼容。

## 验收标准

正式 Evaluation 模块完成时必须满足：

- 每一个分数都能追溯到候选人回答的精确原文；
- 每一条贡献都有 competency、criterion、rubric anchor 和 reason code；
- 相同输入、相同 rubric 和策略版本可确定性重放；
- 无效 quote、模型失败和超时不产生分数；
- 重复表达不提高 evidence count 或 score；
- 未覆盖关键能力时不输出 overall score；
- Evaluation 失败不会错误结束 topic 或面试；
- 重新评分创建新 snapshot，不覆盖历史结果；
- 最终报告能展示纳入、排除、矛盾和证据缺口；
- 当前 Evaluation recovery、dialogue evaluation 和 interview planning 回归测试持续通过。

## 暂不开发的内容

在核心契约、rubric 和聚合策略稳定前，不开发：

- 基于分数的自动录用决策；
- 候选人排名；
- 将简历 claim 直接作为评分证据；
- 让 Question Agent 读取完整 rubric 或当前分数；
- 将 LLM 输出的 competency 总分直接写入报告；
- 为了评分模块而引入新的 Agent 框架；
- 在没有人工标注和校准数据前宣称分数可代表录用概率。

## 维护方式

每完成一个阶段，应更新本文档的当前基线、交付状态、未决策项和验收结果。如果契约、rubric 或聚合策略发生变更，必须同时记录版本号和迁移方式，避免历史 score snapshot 无法重放。
