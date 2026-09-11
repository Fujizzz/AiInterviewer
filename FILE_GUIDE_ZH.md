# 合并后代码结构说明

## `main.py`

终端入口。读取命令行参数，调用 `MVPInterviewApplication`，展示问题及最终 JSON 报告。

## `app/`

远端 MVP 保留下来的应用层能力：

- `application.py`：组合简历解析、Agent、Evaluation 和 Repository，驱动终端面试；
- `resume.py`：读取 TXT 或文本型 PDF；
- `llm.py`：OpenAI 和千问结构化输出客户端；
- `agents/resume_parser.py`：将简历转换为本地标准 `CandidateProfile`；
- `agents/evaluator.py`：根据已聚合能力状态生成最终报告；
- `adapters/llm.py`：把远端同步模型客户端适配为本地异步 `LLMPort`；
- `adapters/evaluation.py`：实现逐题证据提取和能力状态聚合；
- `adapters/repository.py`：MVP 内存 Repository，实现原子 turn commit。

旧 `app/graph.py`、`app/state.py`、`question_agent.py` 和 `answer_analyzer.py` 已由本地核心替代。

## `agents/`

本地优先保留的决策核心：

- `orchestrator/service.py`：唯一公共 Agent 入口；
- `orchestrator/state_machine.py`：面试阶段迁移；
- `orchestrator/termination.py`：结束策略；
- `policies/`：能力、项目、主题、难度、追问、anchor 和去重策略；
- `question/`：问题规划、生成、校验和 fallback；
- `routing/`：RAG 请求路由与安全上下文拼装；
- `ports/`：LLM、RAG、Evaluation、Repository 边界；
- `config/`：可验证的策略配置；
- `domain/`：Agent 内部持久化与日志模型。

## `shared/contracts/`

跨模块唯一数据契约，包括候选人、岗位、问题、回答、能力状态、检索、评价反馈和 Agent 动作。远端旧状态模型不再使用。

## `tests/`

- `contracts/`：端口和共享模型契约测试；
- `unit/`：确定性策略及问题流水线测试；
- `integration/agents/`：Agent 服务、并发、超时和回放测试；
- `test_interview.py`：合并后 MVP 端到端离线测试；
- `test_qwen_pdf.py`：千问结构化输出和 PDF/TXT 读取测试；
- `smoke_interview.py`：完整离线终端演示；
- `live_interview.py`：需要真实 API Key 的手动测试。

## 运行时边界

`app/` 负责输入输出与具体供应商适配，`agents/` 负责所有面试决策，`shared/contracts/` 负责跨模块数据结构。后续接入 Web、数据库或真实 RAG 时，应新增 adapter，不应把供应商代码写入 Agent 策略。
