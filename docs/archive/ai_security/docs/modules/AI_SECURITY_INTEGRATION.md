# AI 安全 v3：主开发侧联调约定

状态：安全模块、面试状态适配器和执行前拦截接口已实现并可调用。按本次确认范围，网页主流程未默认启用；不表示线上请求已受到这些检查。

## 接口与分工

| 接口 | 功能 | 调用方责任 |
|---|---|---|
| `SecurityEngine.check(request)` | 返回 allow / deny / error，用于独立检测 | 明确处理三态，不能把 error 当安全 |
| `SecurityEngine.require_allowed(request)` | deny/error 分别抛 SecurityBlocked / SecurityCheckFailed | 检查后只能使用同一快照 |
| `ai_security.interview.build_interview_request(...)` | 从现有 InterviewContext.state / InterviewState 装配安全请求 | 从后端提供身份、任务、权限、正文和来源 |
| `ai_security.execute_checked(...)` | 检查 → 重读快照 → 比较摘要 → 调用一次操作 | 提供真实刷新和实际执行回调；提交时事务内验证版本与权限 |
| `ai_security.project_provider.create_reviewer()` | 复用 backend/.env 的项目模型配置 | 配置团队认可的模型环境；创建本身不发请求 |

共享安全契约位于 `shared/contracts/security.py`，版本 **3.0**，独立于原 Agent 2.0 契约。
核心库不依赖 Agent 私有实现或数据库；状态适配器只依赖共享 InterviewState。供应商适配器按需导入，负责读取配置，不修改配置或业务参数。

## 字段来源与迁移

| 字段 | 可信来源和约束 |
|---|---|
| context.request_id/session_id/actor_id | 后端认证、资源归属和请求调度；浏览器自报值不是凭证 |
| context.task/phase/recipient | 后端指定的任务、状态和真正接收者，不由候选人正文决定 |
| context.trusted_task | **新增必填**：任务策略版本、业务状态版本、允许的动作、可公开信息、更正/澄清权限 |
| contents[].source/text | 正文始终是不可信数据；来源可为 user/resume/job/rag/tool_result/memory/model_output |
| contents[].readable_by | **新增必填**：后端数据权限标签，candidate/staff/internal；不得从原文自报权限中复制 |
| contents[].derived_from | 本快照内父片段 ID，默认空；引用必须指向前面的片段，不能循环或缺失 |
| action.name/resource_id/authorized/arguments | 即将执行的真实动作；authorized 必须来自业务授权，不由检测模型赋予 |
| SemanticAssessment.reason_code | **新增必填**：no_violation、instruction_override、goal_hijack、data_disclosure、context_missing、intent_ambiguous |
| SecurityDecision.semantic_reason | 记录有效语义原因；准入拒绝、超时和运行错误没有模型原因 |

旧请求必须显式迁移到 3.0；不保留自动补权限的兼容路径。共享契约可直接导出 `model_json_schema()`。
安全策略仍沿用现有例子的字符预算、动作名单和 5 秒语义期限，未改变其数值；策略版本与契约版本是两个不同概念。

任务权限是明确的后端输入，示例与离线基准允许更正、澄清、公开评分维度及本人反馈。这些是假定的测试场景，不会自动变为产品默认权限。
`scoring_dimensions` 仅指宽泛评分维度，不包含内部权重、标准答案；`candidate_feedback` 不包含其他候选人的记录。
适配器接受 Agent 状态 created/active/finished；finished 必须对应 FINISHED 阶段，未知或矛盾状态立即报错。后端数据库的 completed 等状态不可直接冒充 Agent 状态。

## 新行为安全入口

当前建设以“拟执行行为是否在后端允许范围内”为主；新的行为授权请使用
[BehaviorEngine / execute_behavior_checked 接入约定](AI_SECURITY_BEHAVIOR.md)。
下方 v3 接口保留用于已有文本诊断和基准复现，不会自动转换成行为授权或作为新入口的回退。

## v3 执行前接口示意

下面的 state、permissions、approved_action 和 content_envelopes 都由主开发侧生成；不能直接来自 HTTP 请求反序列化。

```python
from ai_security import SecurityEngine, execute_checked
from ai_security.interview import build_interview_request

request = build_interview_request(
    state=context.state,
    request_id=request_id,
    actor_id=authenticated_actor_id,
    task="interview_question",
    checkpoint="action",
    recipient="internal",
    permissions=permissions_from_backend,
    contents=content_envelopes,
    action=approved_action,
)

result = await execute_checked(
    engine,
    request,
    refresh_request=reload_request_from_backend,
    operation=execute_snapshot_in_transaction,
)
```

- `reload_request_from_backend()` 是异步零参函数：重新读取权限、业务状态及真正待执行参数，使用同一个 request_id 重建完整 SecurityRequest。不能只返回旧缓存。
- `execute_snapshot_in_transaction(checked_request)` 是异步函数：仅使用收到的已检查参数，事务内核对 `checked_request.context.trusted_task.state_version` 及资源授权；返回原业务结果。
- `execute_checked` 仅接受 action/output 检查点。输出回调可以是经过适当同步保护的发送器，但发送前后的并发协调仍由主系统负责。
- 快照变化抛 `SecurityContextChanged`；拒绝、未完成检查、刷新异常和取消均不会启动业务回调。业务回调开始后的部分副作用需要业务自己的事务或幂等契约，不由安全库回滚。
- 重读与提交之间仍有竞态。可复用 `agent_repository.commit_turn` 的 expected_state_version 验证；这个接口不宣称实现了数据库原子性或任意代码沙箱。

不需要先调用 require_allowed 再调用 execute_checked，否则会重复送模。两个入口按目的选一个。

## 权限与数据流边界

程序核对真实授权结果、全局及任务动作范围、输出受众、来源交集、字符预算和接口结构；LLM 判断上下文中的指令覆盖、任务劫持与泄露意图。没有恢复关键词或正则攻击分类模式。

派生片段的有效受众是自身标签与所有父片段受众的交集，不自动扩大权限。action/model_output/output 检查点中，**提交的全部内容**必须允许目标受众读取。
这是保守的快照级约束：把内部资料与公开输出混入同一候选人输出请求会拒绝。当前没有自动解密、脱敏后解禁或逐字符污点追踪。
调用方需要正确维护完整来源；删除来源标签、漏传父片段、把含秘密的参数误标为公开都不是 LLM 可替代的权限管理。未声明的数据流无法被本接口自动发现。

## 对接位置与异常传播

| 现有位置 | 后续接入方式 | 本次状态 |
|---|---|---|
| InterviewContext / ContextBuilder | 拼提示词前分离后端任务约束与模型/候选人文本，传 context.state | 已提供适配器，未改业务路径 |
| QuestionAgent._execute_tool | 选定实际 get_history/get_plan/get_project 动作及参数，后端授权后执行前检查 | 已提供执行接口，未扩大工具名单 |
| orchestrator / commit_turn | 安全失败终止本次操作；提交事务校验版本 | 未改既有业务异常与回退 |
| WebSocket / 报告发送 | 对外发送前使用 output 检查点 | 未默认启用，尚未验证流式阻断 |
| resource_gate / 上传 | 保持现有连接与上传容量控制 | 不改变已有预算 |

`react.py` 和 `orchestrator/service.py` 有通用 Exception 捕获与备用生成。未来接线时安全异常必须先明确处理并终止，不能落入这些分支继续生成或提交。
不要让候选人承担检查超时或上下文缺失的攻击标签，也不自动惩罚或封禁。
日志记录有限请求标识、决策、原因、阶段和异常类型，不记录正文、密钥或供应商错误消息；尚未实现持久化 NDR 事件数据库。

## 已验证与未覆盖

单元测试验证权限拒绝、来源传播、非法引用、版本/参数变化、异常传播和业务回调零调用；它们使用替身，不等于生产联调或真实模型准确率。
新增 [SQLite 执行结果测试](../../tests/security/test_execution_outcomes.py) 可作为主开发侧联调参考：
模型即使误放行，业务仍需核对资源归属、允许字段和操作权限，并在提交事务内核对版本；
写入中途失败须整体回滚。测试中的 Sandbox 是隔离示范，没有替主后端实现这些检查，
本地发送表只模拟 outbox，不保证真实 WebSocket/网络发送的原子性。
独立真实模型效果见 [v3 评测](../../ai_security/reports/CONTEXT_V3_EVALUATION.md)，校验范围见 [验证记录](../../ai_security/reports/VALIDATION.md)。

未覆盖：真实 WebSocket/数据库执行闭环、完整自主攻击任务环境、网络 DDoS/分布式限速、跨会话关联、图像注入、完整 DLP、HTML 清洗和生产可用性压测。
