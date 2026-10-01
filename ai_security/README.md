# 面试输入输出安全引擎

本目录只保留当前行为边界主链路。保护对象是系统即将公开的实际输出：输入出现攻击示例，并不直接判不合格；系统服从越权指令、泄露或编造证据才违反边界。

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

旧分类引擎、旧执行器、双重 CLI、通用状态适配器和独立 benchmark 实现已删除；没有保留兼容别名、动态工厂加载或备用路径。历史实验数据、配置、提示词及报告集中保存在 [只读历史归档](../docs/archive/ai_security/README.md)，不再有对应可运行旧引擎，不作为当前能力声明。当前行为测试继续使用冻结的行为案例，未改变其标签。
