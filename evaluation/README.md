# Evaluation 模块

第一阶段提供 Evaluation 内部数据契约和行为锚定 Rubric。当前代码不调用 LLM，
不提取或聚合线上证据，也没有接入 Agent 的评分流程。

## 文件与边界

- `contracts.py`：EvidenceItem、QuoteSpan、EvidenceRelation、CriterionAssessment、
  CompetencyScore、ScoreSnapshot、EvaluationResult，以及 criterion 贡献记录。
- `rubric.py`：Rubric schema、版本化 loader 和 assessment 引用校验。
- `rubrics/1.0.0/`：六个 competency、21 个 criterion、105 个专属 1–5 级行为锚点。
- `../tests/evaluation/`：契约、Rubric、失败输入和示例 fixture 测试。

只依赖 `shared/contracts` 的公共能力枚举、CandidateAnswer 和安全 AnswerAnalysis，
不引用 Agent 内部策略。内部 `schema_version=1.0` 与现有 Agent
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

## 契约语义

- QuoteSpan 使用 **Python Unicode 字符下标**，从 0 开始，右端不包含；不是 UTF-8
  字节偏移或 UTF-16 code unit。保留所有原文空白，不做归一化。一个 claim 支持多个
  有序、不重叠的片段。构造时检查长度，`validate_answer` 再校验回答身份和原文。
- EvidenceItem 只记录候选人的陈述；`reported_experience` 不代表事实已被外部核实。
  `new` 无关联 ID，其他五种关系必须引用不同的已有 evidence ID。
  `independence_group_id` 为后续 resolver 预留；这里不生成 ID、不判断独立性。
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

构造契约成功只证明结构有效，不证明证据可评分。yes/ok、label-only、拒答、
explicit unknown 的语义过滤、证据质量分量、grounding/relevance gate、去重、
矛盾消解、确定性 ID、数值聚合与发布门槛分别在第二至四阶段实现。

## Rubric 版本与校准

`1.0.0` 是待人工标注校准的初始通用 pack，所有 criterion 暂用等权 `1.0`，
不设独立证据数量、coverage 或 reliability 的发布阈值。低等级锚点仅适用于
确有相应行为证据的情况，不能把未回答、信息缺失或无证据自动映射为 level 1。
岗位权重和候选人当前得分不会出现在 Rubric 中。seniority pack 的选择留待后续设计。

已发布 pack 的 criterion ID、anchor ID、行为描述和权重应作为历史版本保存。
修改时新增版本目录，不能原地覆盖用于历史 snapshot 的文件；加载器只按显式版本读取。
重评生成新 snapshot 并链接旧版本，旧数据不迁移为新分数。契约破坏性变更应提升
schema 主版本并增加显式迁移器，当前阶段没有需要迁移的线上数据。

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
