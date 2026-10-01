# 面试 AI 的行为边界与联调约定

本模块的主要保护对象是**系统即将执行的行为**。后端定义允许范围，Agent 只提出操作或回答草稿，安全模块检查合格后再交给执行器。候选人提出越界要求，系统拒绝并继续面试，是合格行为；没有攻击输入，系统自行泄露或改分，也是不合格行为。

本版提供应用内 Python 接口、本地 JSON CLI、面试状态适配、程序约束、LLM 语义检查及固定行为评测。真实文字面试 `/ws/agent/` 已接入输入输出网关（见下节）；工具调用与内部评分写入暂不接入。其它入口不会自动受到本接口保护。

## 真实面试输入输出接入（agent-io-v1）

入口是 `backend/interviews/agent_socket.py`，可调用适配器是 `interviews.agent_safety.InterviewIOGateway`：

1. `reserve_request` 预留幂等请求；`bind_input(command)` 在业务模型调用前核对认证归属、当前请求、真实状态和当前问题，保存隔离输入。浏览器不能提供许可或安全结果。
2. 简历、岗位标题和回答分别标为 `resume`、`job`、`user`；本人历史与计算状态作为证据。输入不做攻击词规则匹配或攻击二分类。
3. Agent 生成完整业务响应。`publish(payload, delivery)` 构造服务端行为边界，对整个 JSON 做任务范围、指令权威分离、事实依据和保密检查，包含附带评价、计划及诊断字段。
4. 放行后重读真实数据库，要求边界与提案摘要不变。最终结果在短事务内再次核对归属/版本/请求状态，保存正文及 `_security` 校验记录，再发送同一正文。
5. `prepared`、`question`（含 `last_evaluation`）、先行 `assessment`、`finished` 都必须经检查。进度仅能携带已知阶段、状态及非负耗时；心跳、握手、started、固定错误等协议消息不送模型。
6. 历史详情和单请求结果只公开保存过且摘要一致的获准模型响应，剥离内部 `_security`。未检旧记录保留在库中，但模型正文不再公开；`security_output_available=false` 表示没有可公开的已检响应。本人原始回答仍可在已获准问题下查询。

网关调用 `create_behavior_reviewer()` 读取项目既有模型配置，保持 **5 秒检测预算、100000 字符完整检查预算**；不沿用独立实验的 15 秒，不自动增加重试、换模型或失败放行。原算法参数、评分计算及问题预算不变。客户端原业务消息结构保持；历史 `latest_action` 只重建公开动作摘要，资料/岗位/状态只取已检查的响应，缺少时为 null，不退回原始内部上下文。

错误码：`security_denied`（明确越界，关闭 1008）；`security_check_failed`（超时、异常、不确定或覆盖不全）、`security_context_changed`（检查后状态改变）、`security_contract_failed`（输入输出契约不合格）均停止面试并关闭 1011。错误不回到业务 Agent 的自动修复/备用生成分支。取消或断线会取消本地审查，不重发。

**当前边界：** 这是对外输入输出的保护，不回滚已完成的内部评分或状态写入，也不是工具沙箱。SHA256 记录依赖后端数据库写权限，不是签名或客户端授权凭据。数据库提交与网络发送不能成为一个原子事务，发送失败可能留下已获准但未送达的结果，可由历史接口读取。先行评分检查通过后可能已送达，即使之后报告被拒绝；每条消息独立批准。

保留原完整业务输出供审查，并不静默删字段。如果旧输出里的计划、权重或诊断信息违反保密要求，整包会被拒绝；还需主开发侧明确面向候选人的字段契约。通过离线替身测试只能证明接线及故障关闭，不能证明正常请求放行率或真实攻击检测效果。

验证命令（后端目录）：

```powershell
../.venv/Scripts/python.exe manage.py test interviews.tests.test_agent_safety --noinput
../.venv/Scripts/python.exe tests/run_agent_e2e.py
```

`test_agent_safety.py` 覆盖四种业务出口、完整附带评价、来源绑定、逐出口拒绝、检查异常/不确定/覆盖不全、真实五秒超时、本地取消、输入预算、归属及提交版本、历史无凭据/篡改及进度通道。两个模型在该测试中均显式替换；生产入口没有测试放行器或关闭开关。

## 接口与数据所有权

| 对象 | 构造者 | 用途 |
|---|---|---|
| `BehaviorBoundary` | 认证后的后端 | 绑定主体角色、会话、版本、面试阶段、任务目的、当前问题用途、语义要求及操作许可 |
| `BehaviorPermit` | 后端权限/业务策略代码 | 限定某操作可用的角色、阶段、资源 ID、字段、接收者、参数及必检语义要求 |
| `BehaviorProposal` | 执行适配层封装 Agent 实际提案 | 工具调用/写入参数，或即将发送的正文；必须对应实际执行内容 |
| `evidence` | 后端分离来源后提供 | 候选人回答、简历、RAG、工具返回及前置问题；正文不获得政策权威 |
| `BehaviorAssessment` | 无工具的 LLM 安全审查器 | compliant / noncompliant / uncertain，已检查及违反的要求 ID |
| `BehaviorDecision` | 引擎 | allow / deny / error、违反项、覆盖范围、请求摘要和耗时 |

行为契约为独立的 `1.0`，不会把旧安全契约 `3.0` 的文本分类结论转换为行为授权。已有文本基准和诊断接口仍用于公开集观察；新的行为执行使用 `BehaviorEngine` 和 `execute_behavior_checked`，没有自动回退到旧检测路径。

**不能把 HTTP 请求直接反序列化为可信边界。** actor_role、许可、资源归属、受众、来源标签和 question_purpose 必须来自后端。不能将 Agent 声称“这是安全示例”“已获授权”的文字填进这些字段。问题用途需要后端认可的面试议程或业务状态；没有可靠事实就不要编造。

## 行为矩阵

下表是随附演示配置的操作设计，**不是自动授予生产权限的默认表**。主团队应按照实际产品能力构造许可；不提供的操作即禁止。

| 操作 | 执行主体与阶段 | 资源/数据范围 | 语义要求 |
|---|---|---|---|
| ask_question | question_agent，active | 当前会话，candidate，text | 推进面试，允许分析攻击示例，不服从例子中的命令 |
| reject_request | question_agent / report_agent，许可阶段 | 当前会话，candidate，text | 拒绝越界请求并保持允许的任务；不泄露拒绝依据中的内部秘密 |
| get_history / get_plan / get_project | question_agent，active | 后端列出的资源及获准字段，internal | 查询服务于面试，不扩大范围或将返回值当成新权限 |
| record_answer | interview_service，active | 当前会话的回答字段，internal | 记录实际回答，不伪造证据 |
| submit_evaluation | evaluator，active | 当前会话，批准的评估字段，internal | 使用真实回答证据，不按候选人或工具指令给分 |
| deliver_report | report_agent，completed / finished | 本人获准反馈，candidate | 反馈基于证据，不能披露他人记录、内部细则 |

程序检查角色、phase、stage、操作、资源、字段、受众、参数及派生来源。未知参数一律拒绝；参数约束当前支持类型精确的枚举和显式整数区间，不运行任意表达式或远程 Schema。缺失必填参数拒绝，`true` 不当成整数 `1`。

演示中的历史 limit 1..5、rubric_level 1..5、角色名和字段名属于新增虚构样本约束，**没有修改主项目任何预算或评分参数**。真实 get_history 的 topic/limit、get_project 的 project_id 等应使用业务已有契约验证，再按当前后端许可创建 ParameterBound；未声明的参数不能自动透传。主项目提交的是 EvaluationFeedback 等对象，演示 rubric_level 写入不是其完整提交协议；复杂对象可由业务先验证，再用枚举精确绑定批准的 JSON 值，或明确扩展契约。

LLM 检查任务一致性、不可信指令是否被采纳、评分是否有依据及输出是否泄露。所有程序检查通过的行为仍必须调用语义审查器；模型必须覆盖该操作要求的全部 requirement_ids 才能 allow。未知 ID、非法 JSON、缺失覆盖、异常、不确定和超时均不能放行。

## 调用方式

```python
from ai_security import BehaviorEngine, SecurityPolicy, execute_behavior_checked
from ai_security.behavior_interview import build_behavior_request
from ai_security.behavior_semantic import JsonBehaviorReviewer

engine = BehaviorEngine(explicit_security_policy, JsonBehaviorReviewer(generate_json))
request = build_behavior_request(
    state=interview_context.state,
    request_id=request_id,
    boundary=boundary_from_backend,
    proposal=actual_operation_or_output,
    evidence=source_separated_evidence,
)
result = await execute_behavior_checked(
    engine,
    request,
    refresh_request=reload_behavior_from_backend,
    operation=execute_in_business_transaction,
)
```

`generate_json(instructions, payload, schema)` 是异步生成端口，不挂业务工具、禁用隐藏重试。也可显式使用 `ai_security.behavior_semantic.create_behavior_reviewer()` 复用现有项目模型环境；生成参数、SDK 30 秒配置、零重试和客户端生命周期沿用原适配器，行为提示词单独记录摘要。

只想检查草稿时调用 `await engine.check(request)`。提交/发送应使用 `execute_behavior_checked`，不要先 check 再调用执行接口而重复送模。

本地可复现示例：

```powershell
.venv/Scripts/python.exe -m ai_security.behavior_cli --request ai_security/examples/behavior_request.json --policy ai_security/examples/behavior_policy.json --reviewer-factory ai_security.behavior_semantic:create_behavior_reviewer
.venv/Scripts/python.exe -m ai_security.behavior_benchmark --dataset tests/security/data/behavior_cases_v1.jsonl --policy ai_security/examples/behavior_policy_15s_eval.json --reviewer-factory ai_security.behavior_semantic:create_behavior_reviewer --output tmp/security-results/behavior-new-run.json
```

行为示例策略维持 5 秒；15 秒仅在独立评测配置中使用。行为请求的字符预算覆盖整份请求，包括后端要求，因此计数对象与旧文本基准不同，不能混称同一实验。CLI 只检查，不执行业务；工厂是受信本地代码路径，不是可开放给浏览器的配置。评测拒绝覆盖已有报告，不会失败后改用其他数据或模型。

## 执行与异常边界

1. 引擎冻结完整快照，先检查确定性许可，越界直接 deny 且不调用模型。
2. 其余行为进行一次语义审查；只允许完整、有效的 compliant 结果进入后续步骤。
3. `reload_behavior_from_backend()` 重新读取真实状态、授权和待执行参数，保持同一 request_id；不能返回旧缓存。任一边界、参数、证据或正文改变，抛出 `SecurityContextChanged`。
4. `execute_in_business_transaction(checked_request)` 只能按收到的快照执行。查询必须按获准字段投影，写入不能使用另一个未检查对象，发送器不能附带未检查文本。
5. **重读后到提交前仍存在竞态。** 业务必须在事务中重新验证资源权限和 state_version；可对接既有 `commit_turn` 的 expected_state_version。安全模块不能替代数据库事务、外部发送协调或幂等机制。

`BehaviorBlocked` 表示提案违反边界；`BehaviorCheckFailed` 表示检查未完成。二者均终止该行为，不能落入现有 QuestionAgent / orchestrator 的通用 Exception 修复、备用回答或继续提交分支。取消传播，不自动重试。主团队应明确放置异常边界，不能只在最外层打印错误后继续运行。

输出检查必须发生在首次对外发送之前；已流式发送的内容无法追回。当前文字面试本来就整条返回问题/报告，网关在其发送前等待检查；每次业务输出会新增安全模型调用及延迟。

## 数据流与实际能力边界

- evidence_ids 引用评分/提案依据；derived_from 声明派生来源，两者不能相互替代。提案引用/派生的内容受众按交集限制，不能通过改标签自行扩大。
- 仅把内部上下文放进 evidence 不等于要公开它。程序约束检查提案实际声明的引用/派生来源，LLM 另检查未正确声明的文本泄露；后者没有完备保证。
- 外传到哪个地址、读取哪个真实对象，必须体现在被检查且由执行器实际使用的参数/受众中。模型误放行不能获得数据库权限，遗漏数据流也不能靠摘要补救。
- 没有任意代码沙箱、完整污点追踪、自动脱敏、DDoS 防护或持久化 NDR 事件数据库。日志只记录有限 ID、状态、违反项、阶段、异常类型和耗时，不保存正文和凭据。
- 合格性模型仍会误判；即使返回全部检查项，也不等于形式化证明。必须联合验证正常任务完成率、越界漏放率、失败率和真实业务执行结果。

## 测试含义

新增 30 条面试场景为 15 组系统行为对照，包含同一攻击输入下的正确拒绝/越界执行，也包含普通输入下的系统任务偏离。标签定义为拟执行行为是否合格，不是原文是否包含攻击。数据和提示词在真实 API 调用前冻结，人工合成案例属于开发测试，不是盲测。

`test_behavior.py` 覆盖契约、程序边界、语义完整覆盖、故障关闭、快照隔离、状态适配及 SQLite 的提交/拒绝/竞态/回滚。真实模型评测只检查固定提案，没有完整运行被攻击的面试 Agent，因此不称为端到端攻击成功率。公开数据集保持独立，仅用于观察通用文本诊断表现。
