# 安全 v3 模块及联调接口验证记录

最新行为安全版本的实现、真实模型结果及本轮检查见
[行为 v1 验证](BEHAVIOR_V1_EVALUATION.md)。本轮项目测试为 351 passed、5 subtests passed，
全项目 Ruff 通过；全量注释检查仍访问冲突，范围内检查通过。下方保留此前 v3 的历次记录。

日期：2026-09-26。工作区为 AiInterviewer，未执行 Git 提交。

## 已完成

| 检查 | 命令或方法 | 结果 |
|---|---|---|
| 全项目 Python lint | `.venv/Scripts/python.exe -m ruff check .` | 通过 |
| 安全模块针对性测试 | `.venv/Scripts/python.exe -m pytest tests/security -q` | 93 passed |
| 项目配置的完整测试集 | `.venv/Scripts/python.exe -m pytest -q` | 304 passed，5 subtests passed |
| 本地 JSON CLI | 子进程测试注入显式测试工厂 | 覆盖 allow/deny/error、非法输入、无效/缺失工厂 |
| 变更范围注释、目录、变量索引 | 复用现有 check_docs 的函数，见下文 | 0 问题 |
| 文档人工核对 | 逐文件核对职责、目录、输入、返回、状态、副作用与异常处理 | 已完成 |
| 真实模型固定数据评测 | 使用 backend/.env 的 DashScope/qwen-plus，单次调用且不重试 | 结果及失败项见 [v3 评测](CONTEXT_V3_EVALUATION.md)，不覆盖历史报告 |
| 已跟踪文件差异格式 | `git diff --check` | 通过 |

完整测试集指 pyproject.toml 中配置的 `tests/`；不声称运行了未被该配置收集的所有后端专项测试。
所有新 Python 文件均完成声明注释及文件顶部目录的同一工作区变更；尚未提交，未验证 Git 提交原子性。

安全测试共 93 项。新增覆盖任务动作范围、来源交集/非法父引用、已有 InterviewState 适配、可信字段与正文隔离、拒绝/故障时回调零调用、版本/权限/内容/参数变化、异常/取消传播，以及场景评测输入与分母，以及非法场景 CLI 诊断不回显原文。

这些测试使用明确的异步替身；真实 DashScope API 另行评测。OpenAI 分支仅模拟验证。安全模型没有业务工具，不修复非法输出，不重试、不回退，未修改原 5 秒时限、SDK 超时或供应商参数。
新增 v3 任务上下文和提示词属于本次确认的升级范围；原数据、标签、分母及旧报告保留。新增 16 条上下文场景单独评测，不替代原 32/116 条基准；初始 v3 和误报修订 r2 各自保留结果，不把它们合并为一次实验。

## 注释检查的覆盖与失败项

项目没有根目录 `tools/check_docs.py`，实际约定检查器位于 `backend/tools/check_docs.py`。
此前在 backend 目录执行 `../.venv/Scripts/python.exe tools/check_docs.py` 出现 Windows 访问冲突，
退出码 `3221225477`（有符号表示为 `-1073741819`）。本次重跑带 faulthandler 的命令仍报
`Windows fatal exception: access violation`（工具返回退出码 1），未产生正常覆盖报告。

通过 `-X faulthandler -u tools/check_docs.py` 取得调用栈，显示故障发生于原有
`javascript_docs.py` 的 visit/javascript_symbols 遍历路径，随后进入 check_docs。
v3 本次重跑同样发生上述访问冲突。此次没有修改这些文件；尚未定位根因，不声称是框架或运行时缺陷，也未改变检查标准绕过失败。

因此，**后端全量注释检查未通过**。本次新增代码均为 Python，使用同一现有检查器的
`check_documentation` 分别检查 ai_security（11 模块、37 声明）和 tests/security
（8 模块、67 声明）；对新增 shared/contracts/security.py（14 声明）复用其
`iter_definitions`、`module_variables`、`compare_catalog`，并检查各声明 docstring。
该变更范围共 20 个 Python 文件，目录、声明、模块变量索引未发现问题。

人工核对还覆盖：主系统尚未接入、规则路径彻底移除、供应商配置复用、请求快照与嵌套参数、
取消/超时/故障语义、原文与密钥不写日志/报告，以及公开数据与合成样本的来源和实际误判。

## 未验证事项

- 主后端真正执行安全拦截、数据库权限、前端流式发送阻断。
- 真实模型的生产分布准确率、长期可用性及固定服务端版本可复现性；本次小规模评测不能代表这些指标。
- OpenAI 路径的真实网络请求（当前后端使用 DashScope）。
- 图片提示词攻击、DDoS/压力测试、跨会话关联及完整 NDR 能力。
- 原有后端全量注释检查崩溃的根因。

本次未修改既有评分、模型参数、随机种子、数据划分或主系统失败处理；安全模块是可调用初版，
业务启用需要主团队按接入文档联调；execute_checked 不能替代提交时的原子版本校验。


## 后续独立 15 秒实验

在完成以上 v3 代码验证后，用户授权将独立评测时限改为 15 秒，重测同一套 164 条样本。此次只新增实验 JSON 配置和报告并更新文档，没有修改 Python 实现，也没有重复执行上方代码测试。默认策略仍为 5 秒。

本次实际完成：SecurityPolicy 校验、模型/提示词/SDK 配置比对、三组真实 API 全量评测、逐条新旧结果对照、旧报告及原配置/源文件摘要核对、文档链接与差异格式核对。结果和限制见 [15 秒对照报告](TIMEOUT_15S_EVALUATION.md)。未启用生产防护、未提交 Git。

## 后续错误归因与 SQLite 执行验证

在上述实验后，只读检查原始数据与报告的 27 条非正确完成记录，新增
`tests/security/test_execution_outcomes.py`；未修改任何安全生产 Python 文件、模型提示词、
策略或原始数据/标签。原生产源码、5 秒默认策略和历史基线报告均与 15 秒实验清单中的 SHA256 一致。
新增错误清单逐行核对了数据哈希、行号、标签和两次决策，无新的真实 API 调用。

本轮实际检查：

| 检查 | 结果 |
|---|---|
| 新增 SQLite 执行结果测试 | 22 passed |
| `.venv/Scripts/python.exe -m pytest tests/security -q` | 115 passed |
| Ruff check / format --check 新增测试文件 | 均通过 |
| backend 中 `../.venv/Scripts/python.exe -X faulthandler -u tools/check_docs.py` | 再次访问冲突，未通过；栈仍在原 javascript_docs.py 的 visit/GC 路径，根因未定位 |
| 现有 `check_documentation(Path('tests/security'))` | 9 个 Python 模块、87 个声明，0 问题；仅此范围通过，不替代全量检查 |

人工核对了新增文件的 20 项声明索引、输入/输出、SQLite 事务、角色/字段校验、异常回滚和
无真实网络发送的限制。代码、注释和索引在同一工作区变更中交付；没有 Git 提交。
本轮未重跑全项目测试，前面的 304 passed 仍是历史结果，不作为本轮新结果。

详细归因、22 个案例的覆盖及尚未确认的上下文实验见
[错误归因与执行边界验证](ERROR_ATTRIBUTION_V3_R2.md)。语义替身通过不代表新的模型召回率，
测试后端的参数/权限/事务校验也不代表真实后端已经接入。
