# MVP Agent 接入与测试

已实现 `ws://127.0.0.1:8765/ws/agent/`。浏览器测试页为 `http://127.0.0.1:8765/agent/`。
后端复用根目录 `InterviewAgentService`、MVP 模型/评价适配器、内存仓库及报告生成器。
本次源码修改仅位于 `backend/`；`/api/sessions/` 练习流程与 `/ws/echo/` 诊断接口继续独立运行。

## API key 存放位置

在 **`AiInterviewer/backend/.env`** 中填写密钥。首次配置可以复制模板：

```powershell
# 在 backend 目录执行；已有 .env 时不要覆盖，直接编辑。
Copy-Item .env.example .env
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
后端只自动读取 `backend/.env`，不读取根目录 `.env`；已存在的进程环境变量优先，修改文件后需要重启。
仓库现有 `.gitignore` 已忽略 `.env`，只提交无密钥的 `.env.example`。密钥不放在浏览器代码或 WebSocket 请求中。

`DJANGO_SECRET_KEY` 是独立的 Django 应用密钥，不是模型 API key。可在当前 PowerShell 中生成，或把随机值写入 `backend/.env`：

```powershell
python -m pip install -r requirements.txt
$env:DJANGO_SECRET_KEY = python -c "import secrets; print(secrets.token_urlsafe(48))"
python manage.py migrate
python -m uvicorn config.asgi:application --host 127.0.0.1 --port 8765 --ws websockets-sansio
```

打开测试页，粘贴简历文本并开始面试。输入逐题回答，结束后查看评分、报告、完整问答和决策记录。
当前输入为简历文本，尚未提供文件上传、语音识别或语音输出。

## 会话与参数

- 每个 WebSocket 独占一个内存 Agent 会话，断开、取消或完成后释放，不支持恢复或历史查询。
- 简历、回答与报告不写业务数据库、文件或浏览器持久存储；模型调用会将相应内容发送给配置的供应商。
- 默认 `max_questions=5`、`max_follow_up_per_topic=2`、岗位 `General AI / Software Engineer`，沿用 MVP 能力权重与 `project_deep_dive` 阶段。
- 沿用 MVP 逻辑预算：题数 × 120 秒，每次回答扣除 120 秒。这不是实际计时；不改动练习接口的准备 10 秒和回答 90 秒。
- 消息最大 256 KiB，未知字段、空文本、非整数参数等明确报错。
- 同时只执行一个命令。处理中返回 `busy`；重复 UUID 返回 `duplicate_request`；旧问题返回 `stale_question`。不排队或自动重发。

## 实际协议

连接后收到 `hello`，包含 `connection_id`、`max_message_bytes` 和 `seconds_per_question`。
每条客户端命令必须携带唯一 UUID `request_id`。以下 `<UUID>` 是占位符，测试时须替换为实际 UUID。

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
{"type":"finished","request_id":"<同一UUID>","result":{"interview_id":"<面试ID>","interview_finished":true,"question_history":[],"interview_state":{},"final_report":{},"decision_logs":[]}}
```

实际 `result` 与终端 MVP 输出字段一致，包含候选人资料和岗位资料。完成后以 1000 关闭连接。
MVP 使用结构化完整输出，当前没有 token `delta` 协议，不把整段文本拆开伪装为模型流式输出。

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
实际 ASGI 联调完成两题面试及报告，并检查业务数据库保持不变；测试进程使用临时数据库，结束后清理。
这些测试不代表真实模型通过。填好 key 后，使用生产入口 `config.asgi:application` 和 `/agent/` 页面测试实际供应商；不要用测试专用入口测试 key。

2026-09-11 已使用本地配置的千问服务完成一次真实单题 WebSocket 面试，简历解析、出题、评价及报告生成均成功，全程约 20.1 秒。使用虚构测试内容；原始输入、输出和密钥未写入测试记录。详细范围见 `testing.md`。
