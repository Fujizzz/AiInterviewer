# Evaluation 模块

第一阶段提供 Evaluation 内部数据契约和行为锚定 Rubric；第二阶段提供可单独调用的
Conversation Analyzer、Evidence Extractor 和失败恢复编排。当前尚未接入 Agent 的
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
- `model_calls.py`：独立调用期限、schema 重验和阶段化错误边界。
- `service.py`：分析、提取及 fail-closed 编排，返回内部 `EvaluationResult`。
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
Extractor 复用第一阶段的原文/offset 校验，不修补错误 quote。任一条不合法或同批
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

## 契约语义

- QuoteSpan 使用 **Python Unicode 字符下标**，从 0 开始，右端不包含；不是 UTF-8
  字节偏移或 UTF-16 code unit。保留所有原文空白，不做归一化。一个 claim 支持多个
  有序、不重叠的片段。构造时检查长度，`validate_answer` 再校验回答身份和原文。
- EvidenceItem 只记录候选人的陈述；`reported_experience` 不代表事实已被外部核实。
  `new` 无关联 ID，其他五种关系必须引用不同的已有 evidence ID。
  `independence_group_id` 为后续 resolver 预留；第二阶段提取统一输出待解析的 `new`，
  不声明其在语义上独立。历史索引目前仅作上下文，关系解析在第三阶段实现。
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
  历史项。对完整 ledger 的引用存在性、矛盾原文和数学重放校验留待后续阶段。
  `failed` 结果必须有原因，不能携带 evidence、assessment、snapshot 或 topic completion。
  安全对话分析复用现有 AnswerAnalysis；该共享模型本身仍可变。

构造契约成功只证明结构有效，不证明证据可评分。第二阶段输出尚未经过 criterion
匹配、质量权重和发布门槛；证据质量分量、语义去重、独立性、关系/矛盾消解、
数值聚合与发布门槛仍在第三、四阶段实现。Analyzer 的双原文矛盾检查仅用于安全对话反馈，
不能替代 Resolver 对 evidence 关系的判断。

Evidence ID 格式为 `evidence-v1-<sha256>`，输入为 `identity_version=1`、answer_id、
有序的原始 quote/char_start/char_end、normalized_claim 和 evidence_kind；使用
`ensure_ascii=False, sort_keys=True, separators=(",", ":")` 的 UTF-8 JSON。
不归一化原文、偏移或 claim，不纳入 request_id、生成顺序和提取版本。
相同提取内容重放产生相同 ID；重新调用模型仍可能产生不同 claim，不能据此声称
LLM 生成确定性。语义相同但表达不同的 claim 留给第三阶段去重。

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
