# AI Interviewer

远端可运行 MVP 已与本地确定性 Agent 核心合并。当前应用保留简历读取、OpenAI/千问接入和终端交互，同时以 `InterviewAgentService` 作为唯一面试决策中心。

## 当前能力

- 读取 UTF-8 TXT 和文本型 PDF 简历；
- 将简历转换为版本化 `CandidateProfile`、结构化项目和可核验 claims；
- 根据标准能力空间选择 competency、project、topic、difficulty 和 probe depth；
- Planner、Generator、Validator、Fallback 分层生成问题；
- 对每轮回答提取 relevance、evidence strength、confidence 和 rubric level；
- 原子提交状态、问题、反馈和决策日志，重复反馈保持幂等；
- 按岗位能力权重在代码中计算最终分数，LLM 仅负责报告文字；
- 支持 OpenAI 和千问 DashScope；
- 支持网页数字人面试、英文问题朗读、语音转录和可编辑回答确认；
- 输出问题历史、能力状态、决策日志和 JSON 报告。

## 架构

```text
TXT / PDF resume
       ↓
MVP application shell (app/)
       ↓
canonical contracts (shared/contracts/)
       ↓
InterviewAgentService (agents/)
       ├── deterministic policies
       ├── LLMPort → OpenAI / Qwen adapter
       ├── EvaluationPort → evidence adapter
       ├── RAGPort → optional adapter
       └── RepositoryPort → in-memory MVP adapter
```

旧版 LangGraph 流程、旧 `TypedDict` 状态和独立问题路由已移除，避免出现两个决策中心。

## 仓库模块

```text
agents/      Agent 决策核心
app/         当前终端 MVP 与临时适配器
shared/      跨模块版本化契约
backend/     Django/DRF API、Agent 接入及数字人语音服务
  frontend/  当前面试网页、语音交互与 Pixel Streaming 播放器
DigitalHuman/ UE 5.8 MetaHuman 工程、原生运行插件及本机启动工具
frontend/    产品前端团队预留目录
evaluation/  生产 Evaluation 模块预留目录
rag/         生产 RAG 模块预留目录
ai_security/ AI 安全模块预留目录
tests/       按 Agent 和 App 分类的 Python 测试
docs/        架构、模块规范、指南与示例
```

依赖方向和团队上传规则见 [`docs/REPOSITORY_LAYOUT.md`](docs/REPOSITORY_LAYOUT.md)，
中文文件索引见 [`docs/guides/FILE_GUIDE_ZH.md`](docs/guides/FILE_GUIDE_ZH.md)。

## 安装

推荐使用 `uv`：

```bash
uv sync --extra dev
```

也可使用 pip：

```bash
python -m venv .venv
python -m pip install -r requirements.txt
```

## 模型配置

终端 MVP 与 Django 后端统一读取仓库根目录的 `.env`，配置模板也统一为根目录 `.env.example`。
首次配置可在根目录执行 `Copy-Item .env.example .env`；已有文件请直接编辑，避免覆盖。
进程环境变量优先；修改 `.env` 后重启后端。后端启动还需填写独立生成的 `DJANGO_SECRET_KEY`，
语音开关和 TTS/STT 配置也位于同一模板中。

千问：

```dotenv
LLM_PROVIDER=dashscope
DASHSCOPE_MODEL=qwen-plus
DASHSCOPE_API_KEY=你的密钥
```

OpenAI：

```dotenv
LLM_PROVIDER=openai
OPENAI_MODEL=你的模型
OPENAI_API_KEY=你的密钥
```

## 运行

```bash
uv run python main.py resume.pdf \
  --max-questions 5 \
  --max-follow-up-per-topic 2 \
  --job-title "AI Engineer"
```

不提供 `--job-title` 时使用通用 AI / 软件工程岗位和均衡能力权重。

## 独立后端与流式诊断

`backend/` 提供 Django/DRF 练习接口、SQLite 业务存储和只在内存中处理的 WebSocket 音视频回传测试。
`/ws/agent/` 已通过 `AgentSession` 接入 `InterviewAgentService`，复用现有面试决策、模型与评价流程。
当前面试网页、语音交互和数字人播放器统一维护在 `backend/frontend/`；后续由后端团队迁入根目录 `frontend/`。
安装与启动见 [后端说明](backend/README.md)，模块职责和函数注释规范见 [代码阅读指南](backend/docs/code-guide.md)。

## 数字人面试官

使用 UE 5.8 与 MetaHuman 构建面试官角色和固定面试场景，通过 Pixel Streaming 将画面和声音传输到网页。
数字人负责面试呈现和语音交互；问题、追问、回答评价和报告继续由既有 Agent 流程处理。

网页支持自动朗读英文问题、重新朗读、打断朗读、麦克风回答和实时转录。
用户点击“开始回答”录音，点击“结束回答”后检查或修改转录，再点击“确认提交回答”进入下一轮。
控制器提供 Idle、Listening、Thinking、Speaking、Interrupted 状态，供角色动画扩展使用。

| 目录 | 职责 |
| --- | --- |
| `DigitalHuman/` | MetaHuman 角色、`L_Interview` 场景、原生音频播放与音频驱动接口 |
| `DigitalHuman/Tools/` | 本机信令、串流依赖安装、UE 启动和 Windows 打包 |
| `backend/interviews/speech/` | 百炼 TTS/STT、临时 WAV 与语音 WebSocket |
| `backend/frontend/` | 面试页面、语音交互、PCM 采集与可编辑转录 |
| `backend/frontend/digital-human/` | UE 5.8 官方 Pixel Streaming SDK 包装、构建配置与前端测试 |

语音配置与模型密钥统一放在根目录 `.env`：

```dotenv
SPEECH_ENABLED=true
SPEECH_TTS_MODEL=qwen3-tts-flash-realtime
SPEECH_TTS_VOICE=Cherry
SPEECH_STT_MODEL=qwen-audio-3.1-asr-flash-streaming
```

使用新加坡业务空间的 `DASHSCOPE_API_KEY`；语音 WebSocket 地址与文本模型的 `DASHSCOPE_BASE_URL` 分别管理。
配置模板见 [`.env.example`](.env.example)，密钥只由服务端读取。

首次准备依赖，在仓库根目录执行：

```powershell
python -m pip install -r backend/requirements.txt
.\DigitalHuman\Tools\setup-streaming.ps1
```

在三个终端分别启动后端、信令和数字人：

```powershell
# 终端 1：后端语音与面试接口。
python -m uvicorn config.asgi:application --app-dir backend --host 127.0.0.1 --port 8765 --ws websockets-sansio

# 终端 2：本机 Pixel Streaming 信令。
.\DigitalHuman\Tools\start-signalling.ps1

# 终端 3：已打包数字人程序；开发时可追加 -EditorGame。
.\DigitalHuman\Tools\start-digital-human.ps1
```

打开 [数字人面试页面](http://127.0.0.1:8765/agent/)，点击“连接／播放数字人”，填写简历后开始面试。
角色组装、场景配置与打包步骤见 [数字人操作说明](docs/guides/DIGITAL_HUMAN_SETUP.md)，

## 测试

```bash
uv run pytest -q
uv run ruff check .
python -m tests.app.smoke_interview
```

离线测试不会调用真实模型。`tests/app/live_interview.py` 是需要主动运行的真实 API 测试入口。

## 已知边界

- PDF 只支持可提取文本，不包含 OCR；
- MVP Repository 仍是内存实现，关闭进程后状态不会保留；
- RAG 端口和数据库端口已定义，生产适配器仍需由对应模块接入；
- 最终报告是辅助评估结果，不应直接作为自动化录用决定。

## 回滚点

合并前的本地 Agent 原始版本保存在：

- 分支：`codex/local-pre-mvp-merge-20260911`
- 标签：`local-pre-mvp-merge-20260911`

查看原始版本：

```bash
git switch codex/local-pre-mvp-merge-20260911
```
