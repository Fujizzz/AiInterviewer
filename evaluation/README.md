# Evaluation 模块

第一阶段提供 Evaluation 内部数据契约和行为锚定 Rubric；第二阶段提供可单独调用的
Conversation Analyzer、Evidence Extractor 和失败恢复编排；第三阶段提供原文校验、
Evidence Resolver、确定性关系重放及 criterion 贡献门禁。当前尚未接入 Agent 的
线上评分流程，不执行 Rubric Judge、分数聚合或数据库写入。

## 文件与边界

- `contracts.py`：EvidenceItem、QuoteSpan、EvidenceRelation、CriterionAssessment、
  CompetencyScore、ScoreSnapshot、EvaluationResult，以及 criterion 贡献记录。
- `rubric.py`：Rubric schema、版本化 loader 和 assessment 引用校验。
- `rubrics/1.0.0/`：六个 competency、21 个 criterion、105 个专属 1–5 级行为锚点。
- `inputs.py`：冻结的问题、回答、Planner 目标、当前 thread 历史和精简 evidence 索引。
- `analyzer.py`、`prompts/conversation_analyzer_v1.md`：独立对话分析 schema 和 Prompt。
- `extractor.py`、`prompts/evidence_extractor_v1.md`：原子 claim、多 quote span 和提取 schema。
- `ids.py`：规范 JSON + SHA-256 的确定性 evidence ID。
- `validator.py`：当前及历史证据的原文、身份、非回答硬校验，保守 claim 规范化。
- `resolution.py`：原始来源、关系决策、解析历史、证据状态和矛盾记录契约。
- `resolver.py`、`prompts/evidence_resolver_v1.md`：独立关系提议、引用校验和纯程序重放。
- `eligibility.py`：criterion 局部门禁，每个独立组最多一个完整贡献的确定性选择。
- `model_calls.py`：独立调用期限、schema 重验和阶段化错误边界。
- `service.py`：保留提取入口，并提供三阶段 `evaluate_resolved` 及 fail-closed 恢复。
- `../tests/evaluation/`：契约、Rubric、提取、失败恢复及 Planner 集成回归。

数据模型依赖 `shared/contracts` 的公共契约；运行组件复用现有
`app/providers/llm.py` 的 StructuredLLM 和 `agents/model_calls.py` 的超时/取消机制，
不引用 Agent 内部决策策略。内部 `schema_version=1.0` 与现有 Agent
`contract_version=2.0` 是独立的版本空间，不构成替换或迁移。
`EvaluationResult` 包含内部评分数据，不能直接提供给 Question Agent。
后续第五阶段才实现 `agents/ports/evaluation.py` 并映射到公共接口。
现有 `app/adapters/evaluation.py`、Planner 和最终报告保持原有行为。

## 使用

```python
from evaluation.contracts import CriterionAssessment, EvidenceItem
from evaluation.rubric import load_rubric_pack

pack = load_rubric_pack("1.0.0")
rubric = pack.for_competency("debugging")

# evidence_payload、assessment_payload 来自待校验的内部数据。
# current_answer 是 shared.contracts.CandidateAnswer。
evidence = EvidenceItem.model_validate(evidence_payload)
evidence.validate_answer(current_answer)
assessment = CriterionAssessment.model_validate(assessment_payload)
pack.validate_assessment(assessment)
```

`load_rubric(path)` 校验单个文件；`load_rubric_pack(version, root=...)` 要求指定版本
包含完整六项能力、文件名与能力一致、所有文件版本一致，不会回退到其他版本。
额外字段、重复 YAML key、未知能力、重复 criterion/anchor、缺失等级、非正权重和
跨 criterion 的 anchor 引用都会被拒绝。模型均可通过 `model_json_schema()` 导出 schema。

### 第二阶段调用

```python
from evaluation.inputs import EvaluationInput
from evaluation.service import EvaluationService

# request: shared EvaluationRequest；context: 调用方已获取的 InterviewContext。
# evidence_ledger 必须属于本次 interview，不能传旧版 DimensionEvidence。
topic = next(
    (t for t in context.plan.topics
     if (t.project_id, t.topic_key) == (request.question.project_id, request.question.topic_key)),
    None,
)
evaluation_input = EvaluationInput.from_request(
    request,
    topic=topic,
    history=((entry.question, entry.answer) for entry in context.question_history if entry.answer),
    existing_evidence=evidence_ledger,
)
service = EvaluationService(
    provider,  # 已配置的 StructuredLLM，例如 OpenAILLM；不在模块内创建客户端。
    analyzer_timeout_seconds=30,
    extractor_timeout_seconds=30,
)
result = await service.evaluate(evaluation_input)
```

`extractor_llm=` 可指定另一模型客户端；两个阶段默认各有 30 秒期限，顺序执行，
最坏模型调用耗时约为两者期限之和。调用方可按总预算分配这两个期限。
线程中的迟到结果不会重新发布；外部取消仍传播，不转换成候选人反馈。

输入从共享可变对象复制为冻结的快照。历史按调用方提供的时间顺序保留最近 10 条
当前 interview、project、thread 的回答，排除当前 answer；证据索引最多保留同 thread
的最近 50 条历史项，只含身份和 claim。EvidenceItem 暂无 interview_id，调用方须先
按 interview 隔离 ledger。身份冲突、历史重复 ID 或不匹配的 Planner topic 属于输入错误，
在构造时拒绝；不偷偷使用其他话题的 objective。没有 Planner topic 时允许提取，
但保守地保持 `thread_complete=false`。

Analyzer 只读取问题、回答、thread 历史及 objective/completion_criteria。
Extractor 只读取问题、回答、历史、能力 taxonomy 和精简 evidence 索引；不读取
Analyzer 结论、Planner 完成判断、岗位权重、当前分数、简历或录用判断。
单个 claim 可引用多个不连续原文片段，不同动作、决策、结果按 Prompt 拆成独立项。
同一能力可以有多个 claim；模型不能填写 evidence ID、relation、独立组或分数。

两个输出 schema 均禁止多余字段，并在 provider 返回后重新校验。
Extractor 使用 `validator.py` 集中执行原文/offset 和身份校验，不修补错误 quote。任一条不合法或同批
重复 ID 都使本次提取整体失败，不能保留其他成功片段。常见 yes/ok、裸英文技术标签、
明确不知道或拒答有程序过滤；其他语义标签（含中文）由独立 Analyzer 分类和提取 Prompt
约束。程序过滤不等于通用语义验证，claim 原子性和忠实度仍需后续标注集评估。

任一模型失败、超时或输出非法时，`EvaluationService` 返回 `status=failed`、空 evidence、
空 assessments、无 snapshot，以及 `thread_complete=false` 的安全分析。
reason code 为 `analyzer_*` / `extractor_*`，后缀是 `timeout`、`model_error`、
`invalid_output` 或 `invalid_evidence`。无证据但调用成功时返回 `completed`，
这里仅表示处理成功，仍不会完成 topic 或产生分数。服务无持久化副作用，调用方继续
持有原始回答；失败分析接入既有原子 feedback 提交的回归验证了回答保留、topic 不完成、
继续追问和幂等。正式端口映射及存储接入仍在第五阶段。

### 第三阶段调用与重放

```python
from evaluation.resolution import ResolutionHistory
from evaluation.resolver import replay_resolution
from evaluation.eligibility import plan_criterion_contributions

# 第一次调用使用空历史；后续使用上一次成功结果的 resolution.history。
# history 是本次回答之前的完整来源和决策，不能只传精简的 evidence index。
history = ResolutionHistory(interview_id=evaluation_input.interview_id)
resolved = await service.evaluate_resolved(evaluation_input, history=history)
result = resolved.evaluation
if result.status == "completed":
    history = resolved.resolution.history
    replayed = replay_resolution(history)  # 纯程序，不调用 LLM
    assert replayed == resolved.resolution

    # judge_assessments 由后续第四阶段 Judge 提供；这里不生成等级或分数。
    plan = plan_criterion_contributions(judge_assessments, replayed, rubric=pack)
```

也可直接调用 `EvidenceResolver(provider).resolve(evaluation_input, raw_evidence,
history=history)`。`ResolutionHistory.sources` 保存原始、未解析的 EvidenceItem 及其
Question/Answer 快照；`decisions` 保存模型提议。必须按时间顺序提供完整历史，调用方负责
完整性和 interview 隔离。Resolver 会检查 interview、question、answer、thread、project、
原文片段及身份冲突，并核对输入索引和历史快照。不会截断历史后把遗漏项当作独立证据。
重放同一回答须使用相同的回答前历史；将当前回答再追加到已含它的历史会被拒绝。

模型只能提供关系、同一事件引用、独立性是否明确、撤回原文及简短审计摘要。不存在的引用、
自引用、向未来引用、跨项目合并、遗漏/重复决策、虚构原文和无效撤回片段均使整个阶段失败。
`duplicate/refines/contradicts/retracts` 必须属于同一事件；`supports` 可来自独立事件。
同一项目、thread 或 competency 不能单独证明事件相同；不同问题也不能证明独立。
模型无法确认独立性时输出 `unresolved`，相应组不能贡献。

`canonical_claim` 仅做 Unicode NFC、空白合并与首尾去空白；保留大小写、标点、数字和否定。
不修改原始 `normalized_claim`、quote、offset 或 evidence identity v1。程序强制将同一事件的
规范化重复 claim，以及同一回答中原文重叠的相同 claim 标记为 duplicate；其他语义关系由
独立模型提出。同一事件的多个原子事实和追问补充共用 group，独立经历分别保留。
group ID 由 interview ID 与该组最早 source evidence ID 的规范 JSON SHA-256 生成；
合并旧组时沿用最早来源的 group ID，旧历史保持不变，生成新的解析视图。

未解决矛盾同时保留两个真实原文引用，状态为 `disputed`；duplicate 别名和依赖该 claim 的
refines 也不能绕过门禁。同一事件里无关的事实不被连带阻断。仅显式 `retracts` 消解被撤回
陈述关联的矛盾，保留冲突记录为 `resolved_by_retraction`；不会按“后说的覆盖先说的”处理。
撤回声明本身不贡献，duplicate 别名与依赖补充一同失效；新的替代事实应另行提取。

`plan_criterion_contributions` 验证 Rubric 与 evidence 引用并重放来源，不信任外部传入的
派生状态。受矛盾影响的 assessment 变为 `disputed`，撤回/独立性未明变为 `insufficient`，
仅含重复证据变为 `excluded`；等级和 anchors 清空，不生成 0 分。原 assessment 不变，
门禁结果有新的确定性 ID；已消解的旧 disputed assessment 仍需 Judge 重新评估。
每个 criterion、每个 group 最多保留一个完整贡献。选择顺序为 specificity、ownership、
factuality 的版本化序，再以 evidence/assessment ID 稳定打破平局，不依据 level 挑高分。
其他片段保留为 supplemental evidence；独立经历可以分别贡献，同组也可支持不同 criterion。
此排序是首版可审计规则，不是已校准的 effective weight 或质量分量模型。

`resolver_llm=` 和 `resolver_timeout_seconds=` 可独立配置（默认共用 provider、30 秒）。
三阶段顺序执行，总调用预算为三者之和；失败返回 `resolver_timeout/model_error/
invalid_output/invalid_evidence/invalid_relations`，无本次 evidence、assessment、snapshot
或 resolution，保留原回答与已有历史。无新证据、仅重复、独立性未明或当前证据存在矛盾时
不完成 topic；外部取消传播，迟到模型结果不发布。`evaluate()` 仍是第二阶段提取入口，
明确选择 `evaluate_resolved()` 才执行第三阶段。

原文和引用校验不等于语义正确性证明：事件同一性、改写重复、矛盾和显式撤回的语义判断
仍依赖模型，需后续人工标注校准。测试使用模型替身，未调用在线模型。

## 契约语义

- QuoteSpan 使用 **Python Unicode 字符下标**，从 0 开始，右端不包含；不是 UTF-8
  字节偏移或 UTF-16 code unit。保留所有原文空白，不做归一化。一个 claim 支持多个
  有序、不重叠的片段。构造时检查长度，`validate_answer` 再校验回答身份和原文。
- EvidenceItem 只记录候选人的陈述；`reported_experience` 不代表事实已被外部核实。
  `new` 无关联 ID，其他五种关系必须引用不同的已有 evidence ID。
  第二阶段提取统一输出待解析的 `new`，不声明其在语义上独立。
  第三阶段 Resolver 生成 relation 和 `independence_group_id` 的新派生视图；原始来源不变。
- CriterionAssessment 的 `included` 必须有证据、等级和匹配锚点。
  `insufficient`、`excluded`、`disputed` 的等级均为 null，不把缺失或矛盾变成 0 分。
  `disputed` 必须提供互不相同的正向与反向证据引用。reason code 必须非空；
  `concise_rationale` 是简短审计摘要，不要求隐藏推理过程。
- 分数为 1–5，coverage/reliability 为 0–1，拒绝 NaN/Infinity。
  reliability 为 null 表示尚无可用的估计方法，不暗示可靠性为 0。
  未发布分数必须为 null 且提供 reason codes；发布 criterion 必须存在有效贡献。
- Snapshot 保留所有六项能力，包括未覆盖项；每个 criterion 可记录 assessment、
  evidence、独立组、等级、有效权重和原因，保留 rubric 与聚合策略版本。
  重评需使用新 snapshot ID，并同时提供 `supersedes_snapshot_id` 与重评原因。
  模型冻结并使用 tuple 保存集合；真正的 append-only 存储约束由第五阶段实现。
- EvaluationResult 的 evidence/assessments 是本次新增 ledger 项，snapshot 可以引用
  历史项。第三阶段校验完整来源引用和矛盾原文；分数数学重放属于第四阶段。
  `failed` 结果必须有原因，不能携带 evidence、assessment、snapshot 或 topic completion。
  安全对话分析复用现有 AnswerAnalysis；该共享模型本身仍可变。

构造契约成功只证明结构有效，不证明证据可评分。第二阶段输出尚未经过 criterion
匹配、质量权重和发布门槛；第三阶段完成关系解析与贡献门禁，完整证据质量分量、
数值聚合与发布门槛仍在第四阶段实现。Analyzer 的双原文矛盾检查仅用于安全对话反馈，
不能替代 Resolver 对 evidence 关系的判断。

Evidence ID 格式为 `evidence-v1-<sha256>`，输入为 `identity_version=1`、answer_id、
有序的原始 quote/char_start/char_end、normalized_claim 和 evidence_kind；使用
`ensure_ascii=False, sort_keys=True, separators=(",", ":")` 的 UTF-8 JSON。
不归一化原文、偏移或 claim，不纳入 request_id、生成顺序和提取版本。
相同提取内容重放产生相同 ID；重新调用模型仍可能产生不同 claim，不能据此声称
LLM 生成确定性。语义相同但表达不同的 claim 由第三阶段 Resolver 提议去重。

## Rubric 版本与校准

`1.0.0` 是待人工标注校准的初始通用 pack，所有 criterion 暂用等权 `1.0`，
不设独立证据数量、coverage 或 reliability 的发布阈值。低等级锚点仅适用于
确有相应行为证据的情况，不能把未回答、信息缺失或无证据自动映射为 level 1。
岗位权重和候选人当前得分不会出现在 Rubric 中。seniority pack 的选择留待后续设计。

已发布 pack 的 criterion ID、anchor ID、行为描述和权重应作为历史版本保存。
修改时新增版本目录，不能原地覆盖用于历史 snapshot 的文件；加载器只按显式版本读取。
重评生成新 snapshot 并链接旧版本，旧数据不迁移为新分数。契约破坏性变更应提升
schema 主版本并增加显式迁移器，当前阶段没有需要迁移的线上数据。

第二阶段新增 `analyzer-1.0.0`、`extractor-1.0.0` 和 evidence identity v1；
既有内部 schema `1.0`、Rubric `1.0.0`、共享 Agent contract `2.0` 不变。
新 schema 为内部模型调用输出，不扩展或替换线上 `EvaluationFeedback`。

第三阶段新增 `resolver-1.0.0`、`claim-nfc-whitespace-1` 和 `episode-selection-1.0.0`，
分别标识解析历史/Prompt、规范化键与贡献选择规则；既有版本全部不变，无线上数据迁移。
新增的 Resolution/Eligibility 契约单独记录版本；未来规则变更必须新增版本与重评结果，
不能以新算法重新解释已保存的旧历史。持久化 append-only、CAS 和 snapshot 在第四、五阶段。

## 示例与验证

`../tests/evaluation/fixtures/evidence_to_assessment.json` 展示同一回答中的不连续
quote spans、可纳入的 debugging 证据、因 team-only 被排除的 ownership 证据，
以及 coverage 不足时 overall score 为 null 的 snapshot。
示例 ID、权重和分数由人工构造，`fixture-only-1` 不是可运行或已校准的评分策略。

```bash
uv run pytest -q tests/evaluation
uv run ruff check .
uv run pytest -q
```
