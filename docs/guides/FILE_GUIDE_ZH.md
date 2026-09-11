# 仓库文件说明

仓库现在按团队模块和运行层次组织。当前整理只明确目录归属，没有新增前后端接口对接。

## 顶层目录

| 目录 | 所有者与用途 | 当前状态 |
|---|---|---|
| `agents/` | Agent 团队：面试规划与确定性决策核心 | 已实现 |
| `app/` | 当前终端 MVP：输入输出、组合根和临时适配器 | 已实现 |
| `shared/` | 全团队共享的版本化数据契约 | 已实现 |
| `backend/` | 后端团队：Django/DRF API、持久化与流式诊断 | 已上传，尚未对接 Agent |
| `frontend/` | 前端团队：上传、面试交互和报告界面 | 待上传 |
| `evaluation/` | Evaluation 团队：证据与标准量表评分 | 待上传 |
| `rag/` | RAG 团队：知识摄取与检索实现 | 待上传 |
| `ai_security/` | AI 安全团队：输入输出、访问控制及滥用防护 | 目录已预留 |
| `tests/` | Python 契约、单元、集成和应用测试 | 已整理 |
| `docs/` | 总体架构、模块规范、指南和示例 | 已整理 |

完整依赖方向和上传规则见 `docs/REPOSITORY_LAYOUT.md`。

## `agents/`

- `orchestrator/`：`InterviewAgentService`、状态机、结束与回放；
- `policies/`：competency、project、topic、difficulty、probe、anchor、去重策略；
- `question/`：Planner、Generator、Validator 和 Fallback；
- `routing/`：RAG 路由和安全上下文构建；
- `ports/`：LLM、RAG、Evaluation、Repository 边界；
- `domain/`：Agent 私有持久化对象和错误；
- `config/`：策略配置；
- `prompts/`：版本化 Agent 提示词。

其他模块只应调用 Agent 公共 Service 或实现 Port，不应直接导入 `policies/` 和
`domain/`。

## `app/`

- `cli.py`：终端命令实现；根目录 `main.py` 只是兼容入口；
- `application.py`：当前 MVP use case 和依赖组合；
- `parsing/`：TXT/PDF 读取及标准 CandidateProfile 提取；
- `providers/`：OpenAI、千问模型客户端；
- `reporting/`：最终报告构建；
- `adapters/`：终端 MVP 使用的临时 LLM、Evaluation、Repository 适配器。

未来后端代码不放进 `app/`，而应完全位于 `backend/`。

## `backend/`

- `config/`：Django 设置、HTTP 路由和 ASGI 入口；
- `interviews/api/`：REST 序列化、视图与路由；
- `interviews/streaming/`：内存中的 WebSocket 分片回传协议；
- `interviews/tests/`：Django/ASGI 测试；
- `frontend/`：后端团队自有的流式诊断页，与根目录的产品前端分开；
- `docs/`、`tests/`、`tools/`：后端文档、联调测试和检查工具。

当前后端仍未调用 Agent 决策流程，后续对接应通过公共 Service 和
`shared/contracts/`完成，不直接导入 `agents/` 内部实现。

## `shared/`

`shared/contracts/` 是跨模块数据结构的唯一来源。接口对接前，前端和后端可保留各自
内部模型，但不要复制或直接修改共享契约。

## `tests/`

- `agent/contracts/`：共享模型及 Port 契约测试；
- `agent/unit/`：Agent 确定性策略测试；
- `agent/integration/`：Agent 服务、并发、超时和回放测试；
- `agent/mocks/`：Agent 测试替身；
- `app/`：终端 MVP、模型供应商和简历读取测试。

## `docs/`

- `architecture/`：项目总体架构；
- `modules/`：各模块详细开发规范；
- `guides/`：仓库使用和文件说明；
- `examples/`：不参与运行的示例代码。

## 新代码上传约定

1. 产品前端代码只进入 `frontend/`，后端代码只进入 `backend/`；
2. 后端自有的诊断页统一放在 `backend/diagnostics/`，不与产品前端混放；
3. 各模块可以暂时保留自己的依赖清单和测试工具；
4. 本轮不做跨模块 import 或接口转换；
5. 后续接口对接应作为独立提交完成；
6. 不提交密钥、`.env`、虚拟环境、IDE 设置和构建产物。
