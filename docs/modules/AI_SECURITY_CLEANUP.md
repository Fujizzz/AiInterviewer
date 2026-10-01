# 安全主链路清理记录

日期：2026-09-26。目标：只保留当前面试输入输出的行为边界实现，清理完整的旧分类体系及未使用的实验入口，不调整检测策略或评测结论。

## 最终结构

`ai_security/` 从 17 个 Python 文件收敛到 8 个：`__init__`、`__main__`、`behavior`、`behavior_bounds`、`behavior_semantic`、`project_provider`、`policy`、`errors`。后端仍使用同一 `InterviewIOGateway`，负责四类业务输出和历史公开边界。

- 删除旧分类引擎、语义分类适配器、准入模块、旧状态适配器和旧执行器：engine、semantic、admission、interview、execution。
- 删除未被当前网关使用的 behavior_interview、两套独立 benchmark、重复 behavior_cli 及动态工厂加载 runtime。
- `python -m ai_security` 统一调用 BehaviorEngine 和项目行为审查器，只接受行为请求与显式策略，不提供旧工厂参数或兼容入口。
- ProjectModelTransport 独立于审查语义；行为提示词由 ProjectBehaviorReviewer 提供，不再实例化旧分类器来借用其 generate 方法。
- SecurityContextChanged 移入公共 errors 模块。shared/contracts/security.py 只剩当前仍使用的严格基础模型、标识符、受众及文本来源，不再定义旧分类请求、结论或版本常量。
- 删除旧引擎及退休接口专用测试；供应商请求、取消、错误、用量和配置测试迁移到当前行为链路。CLI 测试改为唯一入口并验证拒绝外部工厂参数。

## 保持不变

- BEHAVIOR_INSTRUCTIONS 的 SHA256 与此前真实 API 连通报告一致。
- 当前保留的 30 条行为回归数据及原行为策略快照与归档逐字节一致，没有更改顺序、标签、指标或删除错误案例。
- 生产网关仍为 5 秒语义预算、100000 字符完整请求预算，模型、温度、SDK 超时及零重试不变。
- 仍审查整包输出，失败停止会话；不新增回退、工具执行或内部评分拦截。
- 行为故障测试维持原 0.02 秒，供应商取消测试维持原 0.1 秒；这些不是生产预算。

历史报告、数据、策略及提示词以原内容移到 [只读归档](../archive/ai_security/README.md)，并保留原目录映射。归档没有可执行旧源码；旧文档中的命令、路径与数量仅表示历史版本，不再是当前接口。没有为继续兼容旧实验而保留第二套生产实现。

## 验证

| 检查 | 实际结果 |
|---|---|
| 核心 pytest 顺序全量复测 | 248 passed，5 subtests passed；其中当前 security 测试 37 项 |
| 后端 interviews 全量测试 | 118 项通过 |
| run_agent_e2e.py | 真实 HTTP/WebSocket、历史、去重、阶段事件及旧接口通过；显式离线模型 |
| Ruff（本次修改 Python） | 通过 |
| 同一项目注释检查器的 Python 检查 | 17 文件、115 声明、0 问题；另人工核对功能说明、索引和状态描述 |
| 全量 tools/check_docs.py | 未完成，仍出现 Windows access violation，退出 -1073741819；没有改动检查器或跳过后声称全量成功 |
| 旧符号与路径扫描 | 非归档 Python 无旧引擎、旧请求、旧执行器、旧适配器或退休入口引用；当前文档无失效入口 |
| git diff --check | 通过 |

首次同时运行核心、后端和端到端测试时，既有 `test_total_deadline_covers_successive_tool_and_model_rounds` 失败：总时限 0.1 秒、模型延迟 0.07 秒，首个工具步骤尚未记录就超时。核查其实际 deadline 实现与断言后，未改代码或参数；单独运行及无并行后台测试的全量复测均通过。该现象与时序敏感一致，尚不能仅凭复测确定根因。

测试数量从 351 降至 248，是删除旧引擎及退休接口测试后的范围变化，不是安全效果提升。本次未重复付费模型评测，离线通过不等于真实攻击识别效果改善；原真实 API 结果留在归档。

本次未创建 Git 提交，不声称已验证同一提交内代码与注释的原子性。
