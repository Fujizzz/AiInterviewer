# AI Interviewer

远端可运行 MVP 已与本地确定性 Agent 核心合并。当前应用保留简历读取、OpenAI/千问接入和终端交互，同时以 `InterviewAgentService` 作为唯一面试决策中心。

## 当前能力

- 读取 UTF-8 TXT 和文本型 PDF 简历；
- 将简历转换为版本化 `CandidateProfile`、结构化项目和可核验 claims；
- 根据标准能力空间选择 competency、project、topic、difficulty 和 probe depth；
- 固定出题计划后，通过有边界的 ReAct 工具循环生成问题，并进行校验与兜底；
- 对每轮回答分析缺失信息与是否追问，并提取多个能力维度的原文证据；不再使用 confidence；
- 原子提交状态、问题、反馈和决策日志，重复反馈保持幂等；
- 按岗位能力权重在代码中计算最终分数，LLM 仅负责报告文字；
- 支持 OpenAI 和千问 DashScope；
- 输出问题历史、能力状态、决策日志和 JSON 报告。

后端另提供支持缺失资料的实验性人岗双向排序接口，使用仓库附带的v4-B树模型，
与面试评分流程独立。模型尚未通过整体效果门槛，分数不是录用概率；
安装、输入输出和调用示例见[推荐模型接入说明](backend/docs/recommendation.md)。

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

ReAct 实现、Windows 启动命令和执行轨迹查看方法见
[ReAct 架构实现与运行指南](docs/ReAct架构实现与运行指南.md)。

## 仓库模块

```text
agents/      Agent 决策核心
app/         当前终端 MVP 与临时适配器
shared/      跨模块版本化契约
backend/     Django/DRF API 与流式诊断后端
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

终端运行默认将关键节点静默保存到 `output/interview_时间戳_唯一编号.md`，每次面试只有一个文件。
记录选题依据、工具调用摘要、最终问题、回答评价、修复/兜底原因和最终结果；
不保存完整 Prompt、State、工具返回正文或额外 JSON 文件。中断时保留已有记录。
可用 `--output-dir` 更改目录；这些记录包含面试资料，已加入 Git 忽略规则。
记录不包含模型未返回的内部推理文本，也不会在面试过程中打印。

## 独立后端与流式诊断

`backend/` 提供 Django/DRF 练习接口、SQLite 业务存储和只在内存中处理的 WebSocket 音视频回传测试。
其中 `/ws/agent/` 已接入上面的 Agent 决策流程，并通过 Django/SQLite 保存面试数据；练习接口与音视频回传诊断仍独立运行。
其浏览器诊断页位于 `backend/frontend/`，与未来根目录 `frontend/` 产品代码分开。
安装与启动见 [后端说明](backend/README.md)，模块职责和函数注释规范见 [代码阅读指南](backend/docs/code-guide.md)。

## 测试

```bash
uv run pytest -q
uv run ruff check .
python -m tests.app.smoke_interview
```

离线测试不会调用真实模型。`tests/app/live_interview.py` 是需要主动运行的真实 API 测试入口。

## 已知边界

- 终端 CLI 的 PDF 只支持可提取文本；后端页面另提供规则提取与多模态转写，见 [PDF 简历解析](backend/docs/resume-pdf.md)；
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
