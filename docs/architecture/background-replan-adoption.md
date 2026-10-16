# 后台动态重规划采用规则

后台任务只生成提案，不发布状态。正常回答流程先处理最新回答和已完成的评估，再尝试采用提案，最后决定下一题。采用过程不新增模型调用，也不等待未完成的后台任务。

## 按话题合并

- 新增且仍可问的目标可以采用；新目标使用 `replace` 不再因收到新回答而整份丢弃。
- 已有目标在当前线程使用中，或期间收到新回答、评估、证据或目标版本变化时，保留当前目标文本、完成条件和证据，仍可采用其顺序、深度和权重偏好。
- 提案遗漏但仍可问的当前线程目标会保留，避免后台结果使当前问题失去目标归属。
- 已关闭、已充分覆盖、次数耗尽或已无可追问信息的话题逐项过滤；其他有效话题仍可采用。
- 模型输出混入已知但不可问的话题时，后台生成阶段也逐项过滤。未知 topic ID、重复 ID 和错误项目归属继续使提案无效。

## 保留的边界

- 计划基础版本必须匹配，不做跨版本合并。
- 携带返回项目授权的提案，在项目访问变化后失效；没有有效授权不能返回已离开的项目。
- 所有时间、容量和项目顺序仍由本地规则按最新状态编译。
- 在独立副本上编译，成功后才更新当前回合的计划、进度和时钟；失败保留现有状态。
- 过滤后没有有效话题、或剩余时间无法安排问题时，丢弃提案并由现有本地规划和结束策略继续。本轮不立即再次请求规划模型；下一轮有新信息时仍可按正常触发规则重新规划。
- 提案保留至事务成功提交，以便状态冲突重算复用同一结果。结束或关闭会取消任务。

## 日志及验证

`planning.background_accepted` 的 `adoption` 区分 `full`、`partial`，并记录过滤/保护项、语义调整和实际预算。`planning.background_discarded` 使用 `STALE_PLAN_VERSION`、`PROJECT_VISIT_CHANGED`、`UNKNOWN_TOPIC`、`NO_VALID_BACKGROUND_TOPICS` 等具体原因；模型失败另记录 `planning.background_unavailable`，生成阶段的详细原因保留在 `planning.fallback`。

实现位于 `agents/planning/background.py`、`reconciliation.py`、`planner.py`，调用策略位于 `agents/orchestrator/service.py`。新增测试位于 `tests/agent/integration/test_background_reconciliation.py`。本次 Agent 集成回归 425 项、Django 自我介绍及 Agent 回归 18 项通过，涉及的文件通过 Ruff 检查。本次没有调用真实 API 或重跑完整面试，模型规划质量和长测中的实际采用率仍需后续验证。
