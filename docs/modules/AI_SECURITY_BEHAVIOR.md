# 面试输入输出行为边界

当前仅有一套生产实现：后端定义允许范围，安全引擎检查系统的完整实际输出，再决定是否公开。原输入攻击分类和通用实验适配路径已删除。

## 工作流程

1. WebSocket 校验命令、重复请求及会话状态，`reserve_request` 预留幂等请求。
2. `InterviewIOGateway.bind_input` 在业务模型调用前核对认证归属、请求类型、真实状态及当前问题；保存独立输入副本。简历、岗位标题、回答分别作为 resume/job/user 来源，不获得政策权威。
3. 业务 Agent 沿用原流程生成完整结果。安全网关不修改业务提示词、评分算法、题数或模型参数。
4. `publish` 从数据库构造 BehaviorBoundary、证据和 BehaviorProposal。程序校验结构、允许操作、角色、阶段、资源、字段、受众、参数及完整请求大小；通过后必须进行语义审查。
5. 安全模型检查 TASK_SCOPE、INPUT_NOT_AUTHORITY、EVIDENCE_GROUNDED、OUTPUT_CONFIDENTIALITY。合格结果必须覆盖全部要求；非法结果、未知要求、不完整覆盖、不确定、异常及超时均不能放行。
6. 放行后重读数据库，完整摘要必须一致。最终响应保存事务再核对归属、版本及 running 状态，保存相同正文和 `_security` 记录，然后发送；先行 assessment 独立检查后发送，不把它保存为最终请求成功。
7. 历史详情/单请求接口仅公开校验记录与摘要一致的获准结果，不从内部 context、问题或评价补回未检模型正文。

## 接入职责

| 组件 | 职责 |
|---|---|
| 浏览器 | 发送简历、岗位与回答，处理固定错误；不能提供许可或安全结论 |
| `agent_socket.py` | 调度单连接请求，分流固定控制事件和待审业务结果 |
| `agent_safety.py` | 数据库状态与来源绑定、固定要求、整体响应检查、交付回调 |
| `BehaviorEngine` | 程序边界与语义检查、完整覆盖、脱敏决策日志 |
| `ProjectBehaviorReviewer` | 把边界、证据和实际提案分离后调用独立模型传输 |
| `ProjectModelTransport` | 沿用项目模型配置和零重试，在同一所属事件循环复用并显式关闭连接池 |
| `agent_records.py` | 幂等请求、带版本的获准结果提交、有限错误及中断状态 |
| `api/agent_history.py` | 归属隔离与已检结果公开 |

覆盖 prepared、question（含 last_evaluation）、assessment、finished。检查整个 JSON，包括附带计划和诊断信息；不只检查显示在页面上的正文。进度仅允许固定阶段、状态和非负耗时，握手、started、取消及固定错误等协议信息不调用模型。

2026-10-04 的安全审查加固将后端许可放入 system 指令区段，证据和完整提案放入 user
数据区段；审查模型 JSON 出现重复字段时返回检查失败，不采用最后一个值。
这次角色分离保持当时的固定规则、完整请求、模型配置和 5 秒时限。独立评测和误拒见
[`security_evaluation/`](../../security_evaluation/README.md)；消息角色分离不构成模型准确率保证。

随后接入的公开数据实验见 [`public/`](../../security_evaluation/public/README.md)。该轮修正
安全语义层的任务约束归属：只判断后端选定要求，面试例子作为条件化说明；按实际正文
检查保密值，拒绝措辞不自动消除披露。规则文本有明确版本哈希和前后源码快照；模型、
权限契约、完整数据、5秒时限及失败语义保持原值。调用增加脱敏阶段耗时统计。

后续优化见 [`optimization/`](../../security_evaluation/optimization/README.md)。程序边界增加
从选定后端要求编译的明确明文保密规则，仅用于拒绝已发生的输出披露；未命中不能放行。
语义审查的私有响应要求一条指向程序生成的实际提案片段的违规引用，再适配为原有
公开评估契约。确定性比较不能覆盖所有编码或间接泄露，片段引用也不能证明语义正确。
完整提案仍传给模型；索引视图仅发送来源、路径及字符位置，原片段保留在服务端，
避免重复发送正文。响应Schema省略说明性注解，原类型、必填字段及运行时校验保留。
私有JSON结论前要求简短事实依据，区分实际动作、拒绝措辞和安全讨论；不进入公开决策
或日志，也不作为程序证明。保留8192字符整体响应上限。
原始提案和证据完整保留，新增片段视图增加输入token；模型仅返回索引，不复制正文。
客户端复用已验证TLS配置，并在初始化阶段加载SDK资源；启动和池关闭成本单独报告，
取消清理当前HTTP流。CLI、评测或网关在停止在途任务后于同一事件循环关闭安全池。
原模型与预算保持。报告保留所有失败原型及独立新任务验收，旧留出样本作为回归使用。

当前统一使用一套固定审查指令`BEHAVIOR_INSTRUCTIONS`，不再根据任务选择专用提示词。
动态后端要求决定审查义务，输入证据不能增加权限；明文拒绝和片段校验仍保留。
已观察的63条公开验收提案仅在`replay`阶段作为回归复测，不能重新称为新留出集。
详情见[统一提示词及清理验证](../../security_evaluation/optimization/UNIFIED_RESULTS.md)。

## 可调用接口

生产适配器：`interviews.agent_safety.InterviewIOGateway`。

```python
# ID 与 owner_id 来自服务端；命令已校验并预留请求。
gateway = InterviewIOGateway(interview_id, owner_id=owner_id, connection_id=connection_id)
try:
    command = await gateway.bind_input(command)
    payload = await business_handler(command)
    await gateway.publish(payload, delivery)
finally:
    # 所属调用者须先停止并等待所有在途任务；关闭发生在同一事件循环。
    await gateway.aclose()
```

`delivery(checked_payload, receipt)` 必须使用收到的已检正文。最终保存须调用 `complete_request(..., receipt=receipt, owner_id=owner_id)`，成功后发送相同正文。数据库校验不能由安全模型替代，回调不能附带未检文本。实际接线见 `agent_socket` 的 `run`、`progress` 和 `deliver_result`。

独立诊断入口统一为：

```powershell
python -m ai_security --request <行为请求.json> --policy <策略.json>
```

请求使用 `BehaviorRequest`，策略使用 `SecurityPolicy`；身份与边界必须由可信后端构造。CLI 读取项目模型配置，只检查、不执行或发送提案，不支持浏览器传入模型工厂路径。

## 状态、失败与边界

- 生产检测预算保持 5 秒、完整请求预算 100000 字符。SDK 沿用既有 Agent 超时并且 max_retries=0，没有备用模型、静默放行、自动修复或重试。
- 明确越界返回 `security_denied` 并关闭 1008。检测未完成、状态变化、契约失败分别为 `security_check_failed`、`security_context_changed`、`security_contract_failed`，停止会话并关闭 1011；安全异常不回到业务自动修复分支。
- 取消/断线会取消本地审查并等待清理，不重发；业务在途同步模型可能仍在远端运行。
- 内部评分/状态可能先于输出检查提交；本接口不回滚这些副作用，工具拦截亦未接入。
- 保留原完整输出接受审查。计划、权重或诊断信息违反保密要求时整包拒绝，仍需主团队确认候选人公开字段契约。
- `_security` 的 SHA256 依赖受信任数据库写权限，不是签名或客户端授权令牌。保存和网络发送不是一个事务，获准结果可能保存后未送达；先行评分送达不保证之后报告也获准。
- 历史无有效记录的模型正文返回 null，不删除原存储。`security_output_available` 表示是否有获准响应；本人输入回答可在已获准问题下查询。`latest_action` 只重建公开摘要，不读取内部动作全文。

## 测试与归档

```powershell
# 项目根目录：行为核心、传输与 CLI
.venv/Scripts/python.exe -m pytest tests/security -q
# backend 目录：真实网关、数据库、历史和协议；显式双模型替身
../.venv/Scripts/python.exe manage.py test interviews.tests.test_agent_safety --noinput
../.venv/Scripts/python.exe tests/run_agent_e2e.py
```

生产源码只保留当前主链路，目录说明见 [模块 README](../../ai_security/README.md)。历史数据与报告见 [只读归档](../archive/ai_security/README.md)。归档中的旧命令与源码路径是历史记录，不能作为当前接入说明；旧实验未重新标注或计算。
