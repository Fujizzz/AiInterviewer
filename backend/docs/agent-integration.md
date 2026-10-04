# MVP Agent 接入与测试

已实现 `ws://127.0.0.1:8765/ws/agent/`。浏览器测试页为 `http://127.0.0.1:8765/agent/`。
后端复用根目录 `InterviewAgentService`、MVP 模型/评价适配器和报告生成器，
通过 `DjangoInterviewRepository` 实现 Agent v1.1 单轮提交；终端 MVP 仍使用其原有内存仓库。
面试适配位于 `backend/`；新增独立的 `agents/resume_cleanup.py` 支持 PDF 视觉转写。
PDF 在个人中心经 HTTP 保存、传统提取或可选视觉校对；面试页传版本 UUID，由后端读取本人已就绪文本。
详见 [PDF 简历解析](resume-pdf.md)。`/api/sessions/` 练习流程与 `/ws/echo/` 诊断接口继续独立运行。

## API key 存放位置

在 **仓库根目录 `AiInterviewer/.env`** 中填写密钥，终端 MVP 和后端共用该文件。首次配置可以复制模板：

```powershell
# 在 backend 目录执行；已有根目录 .env 时不要覆盖，直接编辑。
Copy-Item ../.env.example ../.env
```

OpenAI 配置：

```dotenv
LLM_PROVIDER=openai
OPENAI_API_KEY=填写你的API密钥
OPENAI_MODEL=填写你有权限使用的模型名
OPENAI_TEMPERATURE=0
```

千问配置：

```dotenv
LLM_PROVIDER=dashscope
DASHSCOPE_API_KEY=填写你的API密钥
DASHSCOPE_MODEL=qwen-plus
DASHSCOPE_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
OPENAI_TEMPERATURE=0
```

千问地址需与你的 key 所属区域一致。供应商、模型和 key 均须显式填写；缺少配置时返回 `configuration_error`，不会启用模型替身。
后端自动读取仓库根目录 `.env`，读取路径不依赖启动时所在目录；已存在的进程环境变量优先，修改文件后需要重启。
仓库现有 `.gitignore` 已忽略 `.env`，只提交根目录无密钥的 `.env.example`。密钥不放在浏览器代码或 WebSocket 请求中。

`DJANGO_SECRET_KEY` 是独立的 Django 应用密钥，不是模型 API key。可在当前 PowerShell 中生成，或把随机值写入根目录 `.env`：

```powershell
python -m pip install -r requirements.txt
$env:DJANGO_SECRET_KEY = python -c "import secrets; print(secrets.token_urlsafe(48))"
python manage.py migrate
python -m uvicorn config.asgi:application --host 127.0.0.1 --port 8765 --ws websockets-sansio
```

登录后在 `/resumes/` 保存并核对简历，再到 `/agent/` 选择已就绪版本、填写岗位与预算。
面试页面只调用 start，以版本 UUID 绑定后端输入；语义解析、规划和出题按既有流程执行。
逐题回答时显示真实处理阶段与实际等待秒数；最后一轮先展示数值评分，再补齐报告文字。
后端仍支持独立客户端的互斥 resume_text/resume_version_id 输入及 prepare 协议，页面不重复维护个人资料。
语音识别、问题朗读和独立回答结束检测已接入网页；结束检测的配置、失败语义和同连接 MCP
协议见[独立回答结束检测](answer-completion.md)。

## 会话与参数

- 每个 WebSocket 独占一个运行中的 Agent 会话。资料、问题、回答、上下文、决策和成功响应保存到数据库；断开或取消后可查询历史，但尚不能恢复继续面试。
- 个人中心保存本人 PDF/文本版本，面试绑定版本和输入快照；旧独立文本协议存储行为不变。浏览器不持久化资料，清空面试不删除历史；模型调用会发送相应内容。
- 默认 `max_questions=5`、`max_follow_up_per_topic=2`、岗位 `General AI / Software Engineer`，沿用 MVP 能力权重与 `project_deep_dive` 阶段。
- 沿用 MVP 逻辑预算：题数 × 120 秒，每次回答扣除 120 秒。这不是实际计时；不改动练习接口的准备 10 秒和回答 90 秒。
- 消息最大 256 KiB，未知字段、空文本、非整数参数等明确报错。
- 同时只执行一个命令。处理中返回 `busy`；重复 UUID 返回 `duplicate_request`；旧问题返回 `stale_question`。请求 UUID 全库唯一，每场最多一个 running 请求，重连后也不会重复执行。不排队或自动重发。
- 请求记录在 `started` 前写入，成功响应在网络发送前保存。存储不可用时返回明确错误，不回退内存或启动收费请求。正常退出标记未完成请求为 interrupted；进程骤停可能留下 running，必须人工确认，不自动重放。
- 回答在评价前保存为未评分；评价、Agent 状态、下一动作和决策日志在同一事务提交。若事务失败，原回答保留但评价为空，不作为已评分证据。
- 历史 API 为 `/api/agent-interviews/`，支持列表、详情及请求状态/结果查询，见 [API 文档](api.md)。当前使用 Django 登录会话按本人归属过滤；生产账号隔离与本机匿名开发策略见 [版本接口](resume-versions.md)。

## 实际协议

连接后收到 `hello`，包含 `connection_id`、`max_message_bytes`、`seconds_per_question`
和 `capabilities: ["prepare", "progress", "assessment", "answer_completion_mcp"]`。
每条客户端命令必须携带唯一 UUID `request_id`。以下 `<UUID>` 是占位符，测试时须替换为实际 UUID。

以下旧客户端示例省略 `progress_events`，仍只收到 started 和原有结果事件。
新版网页在每条命令中显式设置 `progress_events: true`，接收下述扩展。

初始化：

```json
{"type":"start","request_id":"<UUID>","resume_text":"简历文本","max_questions":5,"max_follow_up_per_topic":2,"job_title":"General AI / Software Engineer"}
```

服务端先发 `{"type":"started","request_id":"<同一UUID>","operation":"start"}`，再发 `question`：

```json
{"type":"question","request_id":"<同一UUID>","interview_id":"<面试ID>","question_index":1,"question":{"question_id":"<问题ID>","text":"完整问题及其他标准问题字段"},"interview_state":{},"last_evaluation":null}
```

上例仅展示结构；实际 `question` 和 `interview_state` 包含完整共享契约字段。
提交回答：

```json
{"type":"answer","request_id":"<新的UUID>","question_id":"<当前问题ID>","answer_text":"你的回答"}
```

服务端先发 `started`（operation 为 `answer`），然后返回下一题及 `last_evaluation`，或：

```json
{"type":"finished","request_id":"<同一UUID>","result":{"interview_id":"<面试ID>","interview_finished":true,"question_history":[],"interview_state":{},"final_report":{}}}
```

实际 `result` 包含候选人资料、岗位公开资料及报告。`job_profile` 仅包含
`contract_version`、`job_id`、`title`、`seniority`、`domains`；内部评分权重
`competency_importance` 保留在后端，不发送给候选人。最终响应不附带内部计划、决策日志或
评价器控制字段；`question_history` 保留实际问答和个人评价，`interview_state` 保留版本、
完成状态、阶段、题数及耗时。候选人自己的评分、答案引用和改进建议是允许公开的反馈，
内部权重、评分规则、系统提示词和私人参考答案仍受保密规则约束。该公开响应仍完整经过
原有安全审查、收据校验和持久化；历史接口不会返回含内部岗位字段的旧响应。
完成后以 1000 关闭连接。
MVP 使用结构化完整输出，当前没有 token `delta` 协议，不把整段文本拆开伪装为模型流式输出。

### 预解析与真实阶段事件（2026-09-12）

开始面试前可发送：

```json
{"type":"prepare","request_id":"<新的UUID>","resume_text":"简历文本","progress_events":true}
```

依次收到 `started`、解析阶段进度和 `prepared`（含 `candidate_profile`）。这一步不调用
Agent 初始化、不生成问题、不消耗逻辑预算。返回后连接保持开放，可发送带原始文本的 `start`。
精确相同文本只在本连接内复用；修改后重新解析；已开始面试的连接拒绝 `prepare/start`。
不按输入或编辑事件自动发付费请求，不共享跨用户缓存，不设置隐式过期或自动恢复。

设置 `progress_events: true` 的命令会收到绑定同一 `request_id` 的事件：

```json
{"type":"progress","request_id":"<同一UUID>","stage":"resume_parsing","state":"running"}
{"type":"progress","request_id":"<同一UUID>","stage":"resume_parsing","state":"completed","duration_ms":32000}
```

阶段名为 `resume_parsing`、`question_generation`、`answer_evaluation`、`next_action`、
`report_generation`。`next_action` 包含真实 Agent 决策及其可能的出题操作；如果决定结束，
不会伪称生成了下一题。失败或取消阶段不会发送 completed。示例耗时仅说明格式。
网页计时使用实际单调时钟，完全独立于每题 120 秒的逻辑预算；没有虚构百分比或新增请求超时。

最后一轮，在最终状态已提交后、报告模型调用前发送：

```json
{"type":"assessment","request_id":"<同一UUID>","assessment":{"overall_score":3.0,"competencies":{}}}
```

数值由现有报告函数无模型路径计算；这里只提取分数与能力状态，不展示其临时叙述。
随后开始 `report_generation`，最后仍由 `finished.result` 提供完整报告并关闭连接。
assessment 不代表文字报告已成功，也不保证断线恢复；取消/失败时网页保留已返回数值并标记报告未完成。
原 MVP 的文字生成失败回退保持不变。后端观察原始模型调用，新增
`finished.result.report_narrative_status`（`completed` 或 `fallback`）；网页在 fallback 时明确说明
模型文字失败、当前展示既有确定性摘要，数值评分不变。

### 耗时诊断

Session 日志包含真实阶段、耗时及失败/取消类型；模型日志包含 schema、模型名、耗时和已知
`repair_codes`，出题完成日志包含 Agent 返回的生成原因和规划/检索/生成耗时。
`TOO_SHORT` 等代码可以识别为何要求再次生成；日志中的 words 仅复现当前按空格计数的规则，
不把它解释为中文实际词数。没有改变长度阈值、模型、提示词、重试或降级策略。
详细优化边界与跨团队事项见 [性能优化与协作事项](performance.md)。

取消：`{"type":"cancel","request_id":"<新的UUID>"}`，返回 `cancelled` 后关闭连接；直接断开也会取消本地任务。
MVP 的模型适配器在工作线程中调用同步 SDK，所以已经发出的请求可能继续执行至返回或既有超时；不能保证供应商取消计费。
客户端在在途请求结束后释放，不在连接关闭后开始新的一轮模型调用。

错误：`{"type":"error","request_id":"<UUID或null>","code":"...","detail":"可展示的说明"}`。
无法解析或校验的输入不回显原始请求，`request_id` 为 null。协议错误可修正后再发；大小超限关闭 1009。
配置错误和未处理的面试错误关闭 1011，当前会话不可恢复。日志包含连接 ID、请求 ID、模型阶段、异常类型和可用 HTTP 状态码，不记录正文或密钥。

## 既有模型失败语义

继续使用 MVP 的 SDK 60 秒超时与 `max_retries=2`、千问结构校验重试、Agent 既有问题生成重试/备用问题逻辑，以及报告文字失败时的确定性报告。
这些均为原 MVP 行为，后端没有新增自动重试或备用模型，也没有修改 Agent 超时、阈值或评分。
因此收到问题或报告不必然代表每个模型调用成功；需要结合模型调用日志及返回的 Agent 决策记录判断。

## 离线验证与真实模型验证

```powershell
python manage.py test interviews
python tests/run_agent_e2e.py
```

离线测试通过显式测试入口注入固定模型输出，真实 Agent 核心仍参与决策，并与终端 MVP 结果比较。
实际 ASGI 联调完成两题面试、持久化报告及历史查询；只有独立的 WAV/echo 流式诊断阶段检查数据库字节不变。测试进程使用临时数据库，结束后清理。
这些测试不代表真实模型通过。填好 key 后，使用生产入口 `config.asgi:application` 和 `/agent/` 页面测试实际供应商；不要用测试专用入口测试 key。

2026-09-11 已使用本地配置的千问服务完成一次真实单题 WebSocket 面试，简历解析、出题、评价及报告生成均成功，全程约 20.1 秒。使用虚构测试内容；原始输入、输出和密钥未写入测试记录。详细范围见 `testing.md`。

### 实时计划字段

question 除原问题/状态/计划/上一题评价外，还返回 `plan_history`、`topic_progress`、`decision_logs`。
启用安全引擎时，这些字段接受与完整业务响应相同的安全检查；后端保存批准摘要后才发送，不经未检查的 progress
事件透传模型文字。前端利用这些快照展示倒计时、阶段、话题与预计题数，规则及复盘 v2 见
[面试进度与复盘](resume-versions.md#面试进度与复盘2026-10-03)。

### 算法开发期间临时停用安全引擎

后端环境设置 `AI_SECURITY_ENABLED=false` 并重启后，面试安全网关不创建审查模型或安全引擎，
不执行安全扫描预算、确定性安全规则及语义审查。登录、数据归属、协议结构和保存版本校验保留。
结果仍可保存和复盘；内部收据标记 `disabled`，历史请求接口返回 `security_review_status=disabled`。
默认配置为 `true`；重新设为 `true` 并重启即可恢复原有审查与失败拦截，不自动重新审查旧开发记录。
