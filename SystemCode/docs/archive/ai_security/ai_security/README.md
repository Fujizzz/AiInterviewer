# AI Security — 面试行为边界

当前主方向是**约束系统行为，而非给用户输入贴攻击标签**：后端定义允许范围，Agent 提出操作或输出，合格后才执行。新增行为契约 1.0 与 BehaviorEngine；既有 v3 文本诊断接口保留用于公开数据和历史基准复现，二者没有自动回退关系。

## 行为安全入口

- 后端许可限定操作、执行角色、面试阶段、资源、字段、参数和接收者；未知操作及参数拒绝。
- LLM 根据明确的任务要求审查实际提案，判断任务偏离、采纳不可信指令、无依据评分和泄露；输入有攻击但系统正确拒绝，可判合格。
- `BehaviorEngine.check` / `require_allowed` 提供检查；`execute_behavior_checked` 在检查和后端重读一致后执行一次回调。
- `build_behavior_request` 与现有 InterviewState 绑定；真实认证、资源归属、事务版本检查由主后端负责。
- 检查失败、不确定、超时不放行，不自动修复或重试。文字面试 `/ws/agent/` 已接入输入输出检查，工具与内部评分提交不在本次接入范围。

行为契约、面试矩阵、主开发侧接线、异常处理和运行命令见 [行为安全接入文档](../docs/modules/AI_SECURITY_BEHAVIOR.md)。
完整请求示例为 [behavior_request.json](examples/behavior_request.json)，默认示例时限仍为 [5 秒](examples/behavior_policy.json)，独立评测使用 [15 秒](examples/behavior_policy_15s_eval.json)。
首次 30 条面试行为的真实模型结果、超时及程序测试范围见 [行为 v1 验证报告](reports/BEHAVIOR_V1_EVALUATION.md)。

## 独立文本诊断与既有 v3 基准

已移除关键词、正则和固定输出短语的攻击判定，也不再提供无模型检测模式。
**所有通过权限及资源准入的请求必须经过语义检测；已提供项目 LLM 适配器，此旧文本分类入口不接入主业务；真实面试使用上面的行为边界网关。**
旧基线及零召回诊断仅作为[历史证据](reports/README.md)保留，不能代表当前实现的准确率。

## 文本诊断职责

- input/context/model_output/action/output 五类检查点，后端任务权限、片段来源与受众独立于正文。
- InterviewState 状态适配器与 execute_checked 执行前接口；检查通过后重读并比较快照，状态变化拒绝执行。
- 程序执行确定性准入：后端授权结果、全局及任务动作范围、派生来源受众交集、扫描字符预算，以及请求/结果契约校验。
- 必需的异步语义检测端口：根据内容与任务上下文判断风险，不运行词表或正则攻击分类。
- 严格 JSON 适配器：校验有限风险类别、证据引用与结果一致性；异常、不确定和超时均返回 error。
- allow/deny/error 三态、策略版本、请求快照摘要、脱敏日志、Python 接口和本地 CLI。

尚未实现：网络 DDoS 防护、分布式限速/费用配额、跨会话关联、图像攻击检测、
NER/DLP、HTML 清洗和持久化告警。`source=resume` 仅标记已提取文本，不能据此声称扫描了图片。
语义检测通过也不能证明评分正确或内容绝对安全。

## 文本诊断调用（新行为授权请使用上方入口）

```python
from ai_security import SecurityEngine, SecurityPolicy
from ai_security.semantic import JsonSemanticReviewer
from shared.contracts.security import SecurityRequest

# 主系统提供真实异步模型函数并管理模型、凭据及客户端生命周期：
# async generate(instructions: str, payload: dict, schema: dict) -> str
# instructions 放入系统指令；payload 作为不可信结构化数据；返回符合 schema 的 JSON。
# 检测客户端不挂载业务工具，不增加隐藏重试或模型回退。
policy = SecurityPolicy.model_validate_json(policy_json)
reviewer = JsonSemanticReviewer(generate)
engine = SecurityEngine(policy, reviewer)
request = SecurityRequest.model_validate_json(request_json)

# 仅 allow 正常返回；deny 抛 SecurityBlocked，error 抛 SecurityCheckFailed。
decision = await engine.require_allowed(request)
```

也可调用 `await engine.check(request)` 获得三态并自行处理，实际接入选择一个入口，避免重复送模。
使用项目现有配置时，可用 `from ai_security.project_provider import create_reviewer`，
然后以 `SecurityEngine(policy, create_reviewer())` 构造引擎；创建检测器本身不发送模型请求。
缺少 reviewer 或传入无异步 assess 的对象会在构造时失败；不提供自动替代检测器。
越权和超限直接 deny，不调用模型；模型不能推翻权限拒绝。

示例策略见 [semantic_policy.json](examples/semantic_policy.json)，请求见 [request.json](examples/request.json)。
示例数值仅用于演示；不会修改主业务既有阈值。可通过请求及结果类的 `model_json_schema()` 获取接口定义。

`request_digest` 绑定上下文、内容、检查点和动作参数，用于关联和发现变更，**不是签名或授权令牌**。
动作参数改变后必须重新检查。引擎不执行业务动作，也不保存跨会话状态。

## 与主系统的边界及版本迁移

契约升级为 **3.0**：请求必须使用 `contract_version="3.0"`，结果同样返回 3.0。
新增必填 context.trusted_task、contents[].readable_by；derived_from 声明本快照内的父来源。
语义结果新增必填 reason_code，决策新增 semantic_reason；旧请求不能靠自动补权限迁移。
删除旧策略的 `semantic_mode` 和 `protected_output_terms` 字段，旧字段输入会被拒绝，不保留兼容放行。
coverage 改为 `admission_only`（确定性准入拒绝）、`semantic`（完成语义检查）和
`semantic_incomplete`（检测未完成）；allow 必须来自完成的语义检查。
结果字段 `rule_id` 保留名称作为通用检查项标识，不代表仍运行文本规则。

后端负责认证、数据归属、真实授权、工具参数业务校验和让拒绝生效。安全端口不会验证浏览器自报
身份，也不能替代上传限制、文件沙箱或 HTML 安全渲染。输出必须在对外发送前完成检查。
语义输入包含正文、来源、阶段和动作参数，不发送 actor/session/resource 身份字段；正文仍可能有
个人信息，模型环境由团队选择。后端须维护完整来源；当前权限交集不是完整污点追踪或自动脱敏。详细联调位置见[接入约定](../docs/modules/AI_SECURITY_INTEGRATION.md)。

## CLI 与评测

CLI 使用显式 `--reviewer-factory module:function` 加载本地同步无参工厂，返回带异步 assess 的检测器。
该参数允许导入并执行本地代码，只能来自受信配置，不得接受业务请求提供的工厂路径。
项目适配器 `ai_security.project_provider:create_reviewer` 读取 backend/.env，进程环境优先，
复用已有 LLM_PROVIDER、对应 API_KEY/MODEL/BASE_URL、OPENAI_TEMPERATURE 和 Agent SDK 超时。
它不修改环境或业务配置。DashScope 使用项目已有 JSON 模式及 enable_thinking=False；
OpenAI 使用 Responses JSON Schema、store=False。两者均不挂载工具、禁用重试且不修复非法结果。
每次调用创建并关闭异步客户端，外层安全时限继续有效。OpenAI 路径仅经模拟测试，真实运行使用
后端当前配置的 DashScope/qwen-plus。此前 v2 结果见[历史语义报告](reports/SEMANTIC_EVALUATION.md)，本次结果见[v3 评测](reports/CONTEXT_V3_EVALUATION.md)。

```powershell
.venv/Scripts/python.exe -m ai_security --policy ai_security/examples/semantic_policy.json --request ai_security/examples/request.json --reviewer-factory ai_security.project_provider:create_reviewer
.venv/Scripts/python.exe -m ai_security.benchmark --dataset tests/security/data/interview_cases.jsonl --policy ai_security/examples/semantic_policy.json --reviewer-factory ai_security.project_provider:create_reviewer --reviewer-id MODEL_AND_PROMPT_VERSION --output tmp/security-results/interview-semantic.json
uv run --no-sync --with pyarrow python -m ai_security.benchmark --deepset --cache-dir tmp/security-datasets/deepset --policy ai_security/examples/semantic_policy.json --reviewer-factory ai_security.project_provider:create_reviewer --reviewer-id MODEL_AND_PROMPT_VERSION --output tmp/security-results/deepset-semantic.json
```

检测 CLI 退出码：0=allow、2=deny、3=检查未完成、4=输入/配置错误；argparse 参数缺失另以 2 退出并输出用法。
输入/配置异常只输出固定错误码和异常类型，不回显正文。它不是认证 HTTP API。
评测先验证策略和检测器，再下载数据；失败不会改用合成数据。
评测 CLI 的启动/数据/写报告异常只输出有限错误摘要并以 1 退出，不回显 Pydantic 输入值；argparse 用法错误仍为 2。`pyarrow` 仅为临时评测依赖。

数据仍固定原修订、校验和、划分及标签，不训练、不采样、不调参；旧报告不覆盖。
当前评测将准入拒绝单列为 `admission_denied`，检查失败单列为 `errors`，二者均不算语义攻击识别成功。
召回率分母保留全部攻击数，误报率分母保留全部正常数，accuracy 分母保留全部样本数；不会删掉失败样本
抬高指标。这个统计区分与旧版报告的分类路径不同，对比时必须同时检查 coverage 和失败数量。
新报告包含检测器标识、工厂路径、策略和源码摘要。项目适配器还记录模型、生成参数、SDK 版本、
提示词及端点摘要、响应模型和 token 用量；不保存密钥、端点明文或样本正文。模型别名不保证服务端
版本不变，超时请求未返回的用量也无法统计。每条结果保留行号、类别、状态、有限语义原因、错误码和耗时。

新增完整上下文场景单独运行，不合并或替换原 32/116 条基准：

```powershell
.venv/Scripts/python.exe -m ai_security.benchmark --scenarios tests/security/data/contextual_cases_v3.jsonl --policy ai_security/examples/semantic_policy.json --reviewer-factory ai_security.project_provider:create_reviewer --reviewer-id qwen-plus-task-context-v3 --output tmp/security-results/contextual-v3.json
```

程序准入拒绝不会当成语义攻击识别；上下文标签由后端提供，不是原文自行声明的权限。

接口与故障语义测试使用替身；真实模型效果另以固定数据报告，二者不得混用。
评测延迟包括检测调用开销，不代表业务端到端或并发 SLA；已有测试集也不能再作为未接触的盲测。
验证命令及尚未通过的检查见[验证记录](reports/VALIDATION.md)。

现有错误的逐条归因和 SQLite 执行边界测试见[错误分析](reports/ERROR_ATTRIBUTION_V3_R2.md)。
执行测试使用固定语义替身，不代表真实模型准确率；主后端输入输出接入另由 backend/interviews/tests/test_agent_safety.py 验证；默认策略和检测提示词未改。


## 独立 15 秒时限对照

经用户授权，使用 [semantic_policy_15s_eval.json](examples/semantic_policy_15s_eval.json) 对原 164 条样本进行独立重测，仅把语义检测时限改为 15 秒。原 semantic_policy.json 仍为 5 秒，当时网页主流程未启用；当前输入输出接入见行为边界文档。
结果、逐条恢复及延迟代价见 [15 秒对照报告](reports/TIMEOUT_15S_EVALUATION.md)。重现时沿用上面的三条评测命令，把 --policy 指向此独立文件，--reviewer-id 使用 qwen-plus-task-context-v3-r2-timeout15s，并为 --output 指定新的文件名，避免覆盖历史结果。
