# 面试输入输出安全引擎

本目录只保留当前行为边界主链路。保护对象是系统即将公开的实际输出：输入出现攻击示例，并不直接判不合格；系统服从越权指令、泄露或编造证据才违反边界。

本次交付已获用户明确同意按当前不足提交：单提示词模型复测276次，无危险漏放或正常误拒，但长上下文稳定性8次中有2次超时，P95=5014.4毫秒，原5秒验收未通过。并发48次和重放63次尚未执行；仍保留5秒时限、零重试及超时不放行。完整数据与局限见[统一提示词报告](../security_evaluation/optimization/UNIFIED_RESULTS.md)。

生产调用路径：

```text
/ws/agent/
  → InterviewIOGateway.bind_input
  → 业务 Agent 生成完整结果
  → InterviewIOGateway.publish
  → BehaviorEngine（程序边界 + 必需语义审查）
  → 重读状态 → 事务保存获准正文 → 发送
```

| 文件 | 职责 |
|---|---|
| `behavior.py` | 唯一行为引擎、检查异常及检查后执行 |
| `behavior_bounds.py` | 角色、阶段、资源、字段、参数、受众及完整请求预算 |
| `behavior_semantic.py` | 固定行为审查指令、JSON 契约和项目审查器 |
| `behavior_confidentiality.py` | 从选定后端单项保密声明提取完整明文披露规则，只产生拒绝 |
| `behavior_evidence.py` | 从完整实际提案生成不可变片段与位置索引，不包含输入证据 |
| `project_provider.py` | 独立模型传输及配置加载，不携带分类提示词 |
| `policy.py` | 显式动作范围与预算 |
| `errors.py` | 检查与保存共用的状态变化异常 |
| `__init__.py` | 行为引擎的公开接口 |
| `__main__.py` | 使用同一引擎和项目模型的本地检查入口 |

后端适配在 `backend/interviews/agent_safety.py`，共享数据结构在 `shared/contracts/behavior.py`；`shared/contracts/security.py` 仅包含共同的严格模型、标识符、受众及内容来源结构。

## 调用与约束

- 主开发侧调用 `InterviewIOGateway.bind_input(command)` 和 `publish(payload, delivery)`；身份、边界与来源由后端绑定，不能接受浏览器自填权限。
- Python 引擎入口为 `BehaviorEngine.check` / `require_allowed`，执行前接口为 `execute_behavior_checked`。
- CLI：`python -m ai_security --request <行为请求.json> --policy <策略.json>`。输入必须采用当前 `BehaviorRequest` 和显式 `SecurityPolicy`。模型读取既有项目配置，不支持外部 Python 工厂参数；CLI 只检查，不执行提案。
- CLI 退出码：0 合格、2 不合格、3 检查未完成、4 文件/契约/配置错误；缺参由 argparse 返回 2。
- 生产保持 5 秒检测预算、100000 字符完整请求预算、原模型配置和零 SDK 重试。拒绝、异常、超时、不确定均不能发布。
- 工具调用与内部评分提交未接入；输出拒绝不回滚内部状态。先行评分独立审查，可能先于最终报告送达。

完整约定见 [输入输出接入](../docs/modules/AI_SECURITY_BEHAVIOR.md)。

## 验证与历史

当前测试为 `tests/security/` 的行为核心、模型传输和 CLI，以及 `backend/interviews/tests/test_agent_safety.py` 的真实协议/数据库闭环。测试替身不证明真实模型检测效果。

独立的业界方法对照、三态指标、合成在线评测、负载及修复前后证据见
[`security_evaluation/`](../security_evaluation/README.md)。语义审查将后端边界放入 system
指令区段，将完整证据和提案放入 user 数据区段；重复 JSON 字段作为检查失败处理。
这是权限与解析边界加固，不保证消除真实模型的误拒。模型、时限、预算和评分条件保持原值。

当前语义审查统一使用`BEHAVIOR_INSTRUCTIONS`，没有任务专用提示词、选择函数或选择状态。
后端选定的要求仍以动态可信边界传入；单项保密与多项面试规则使用同一审查协议、模型和
JSON契约。固定指令为6270字符，以英文为主并保留必要中文边界说明。相较旧通用版
增加822字符；相较旧保密版增加4341字符。整体压缩原型出现误拒，当前合并保留
义务范围、实际承诺与引用、拒绝与披露的必要区分，删除重复说明而不增加选择分支。
项目只维护一份固定文本，完整证据和提案不变，实际token与时延通过独立回归比较。
模型接收提案片段的字段路径及字符位置，片段正文在服务端保留并校验，避免重复发送。
响应Schema只省略说明性title/description；字段、类型、引用、限制及原校验器不变。
动态明文保密事实用封闭的单值集合明确每项后端规则的保护范围，以及实际出现状态，
不重复固定协议的解释段落。私有事实依据要求最多12个英文词；原JSON校验限制不变。
清理和验证记录见[统一提示词报告](../security_evaluation/optimization/UNIFIED_RESULTS.md)。

公开数据实测见 [`public/`](../security_evaluation/public/README.md)。审查规则只按后端选定
要求判断，面试示例不会额外限制其他授权任务；拒绝措辞中重复受保护值仍属于披露。
传输记录客户端初始化、SDK等待及本地/清理耗时，取消与错误仍为检查未完成。
`generate_text`仅供原始公开任务响应采集显式调用，生产审查继续使用严格JSON入口。

后续架构优化记录位于 [`optimization/`](../security_evaluation/optimization/README.md)。
明确、完整的后端单项保密声明可以编译为明文披露拒绝规则；它只检查实际输出，不从
攻击输入建立秘密，也不产生放行结果。没有确定性违反时仍须完整语义审查。该规则只覆盖
声明语法与完整明文值，编码、拆分和间接泄露继续由语义层判断。

模型的私有响应增加一条决定性违规引用，程序校验它指向真实提案片段后，转为
既有三字段`BehaviorAssessment`；缺失、伪造或不匹配的引用返回检查失败，不能转为合格。
私有结论前要求简短的实际输出事实依据；该依据不公开、不记录日志，也不当成程序
证明。整体模型响应保持8192字符上限，不增加额外的事实依据字段字符硬限。
原始证据和提案不截断。SDK资源模块及验证证书配置在传输初始化时加载并单独记录耗时；
客户端限定同一所属事件循环复用；停止在途任务后显式关闭。取消清理当前HTTP流，
池关闭另行记录。证书验证、原模型参数、5秒时限及零重试均保留。

旧分类引擎、旧执行器、双重 CLI、通用状态适配器和独立 benchmark 实现已删除；没有保留兼容别名、动态工厂加载或备用路径。历史实验数据、配置、提示词及报告集中保存在 [只读历史归档](../docs/archive/ai_security/README.md)，不再有对应可运行旧引擎，不作为当前能力声明。当前行为测试继续使用冻结的行为案例，未改变其标签。
