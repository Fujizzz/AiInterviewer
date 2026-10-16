# RAG 模块接入指南（给组员）

RAG 模块实现了 Agent 的 `RAGPort` 接口（`agents/ports/rag.py`）。CLI 和后端已通过
`app/adapters/rag.py` 接入：当前项目回答一道题后，在追问预算允许时尝试一次技术资料检索，
生成一道原理题。失败则继续普通出题；`RAG_ENABLED=false` 可关闭。环境搭建步骤仍见下文。

负责人：Member 2（RAG & Data）。有问题先看 [`rag/README.md`](../README.md)，再找我。

---

## 1. 搭环境（每台要跑检索的机器做一次）

前提：Docker 已安装并运行，Python 环境已执行 `uv sync`。

```bash
# 1) 装 RAG 依赖（torch / sentence-transformers / psycopg 等，只在需要检索的机器上装）
uv pip install -r rag/requirements.txt

# 2) 配置数据库连接（开发默认值即可，不要提交 .env）
cp rag/.env.example rag/.env

# 3) 启动 Postgres + pgvector（只监听 127.0.0.1:5432）
docker compose -f rag/docker-compose.yml up -d
```

然后二选一导入数据：

### 方式 A：用现成的数据库导出（推荐，几分钟）

找 Member 2 要 `intellihire-rag.dump`（约 81 MB，不在 Git 里），然后：

```bash
docker exec -i intellihire-pg pg_restore -U intellihire -d intellihire \
  --no-owner --no-privileges --clean --if-exists < intellihire-rag.dump

# 检查：应输出 16379
docker exec intellihire-pg psql -U intellihire -d intellihire -Atc "select count(*) from chunks"
```

### 方式 B：从数据源自己重建（约 1 小时，需联网）

```bash
uv run python -m rag.cli migrate              # 建表
uv run python -m rag.cli all --priority P0    # 拉取 → 清洗 → 切块（几十分钟，原始数据约 300 MB）
uv run python -m rag.cli embed --all          # bge-m3 向量化（M 系列 Mac 约 13 分钟；首次下载模型约 2.3 GB）
uv run python -m rag.cli index --all          # 导入数据库
```

两种方式都能用这条命令自测：

```bash
uv run python -m rag.cli search "How does PagedAttention reduce KV cache fragmentation?" --role question_planner
```

---

## 2. 接入 Agent（负责 `app/` 和 `backend/` 的组员）

`InterviewAgentService` 支持 `rag=` 参数，以下两处已传入预热后的共享 adapter：

- `app/application.py`
- `backend/interviews/agent_session.py`

现有入口已自动处理生命周期，不需要重复添加；独立调用服务时可参考：

```python
from rag.adapter import RAGUnavailableError, create_rag_adapter

try:
    rag = create_rag_adapter()
    await rag.warmup()          # 必须调用，见下方说明
except RAGUnavailableError:     # 没装依赖 / 数据库没起 / 没建表
    rag = None                  # 退回无检索模式，面试照常进行

service = InterviewAgentService(
    repository=repository,
    evaluation=evaluation,
    llm=llm,
    settings=settings,
    rag=rag,                    # ← 新增
    background_replanning=True,
)

# 进程退出时
if rag is not None:
    await rag.aclose()
```

### 必须注意

| 事项 | 原因 |
|---|---|
| **启动时必须 `await rag.warmup()`** | 模型加载要几秒，而 Agent 的检索超时是 2 秒（`rag_seconds: 2.0`）。不预热的话，第一次检索一定超时。 |
| **每个进程只创建一个 adapter，全局共用** | 它内部有连接池和模型单例，支持并发调用。 |
| **ReAct 项目原理题已接入** | 每个项目在已回答问题且允许继续追问时尝试一次 `technical` 检索，以固定目标生成原理题；空结果、低置信度或失败会回退，状态保存在 `InterviewContext.project_knowledge`。 |
| **候选人资料按 `candidate_id` 隔离** | 简历入库时用的 `candidate_id` 必须和 `RetrievalRequest.candidate_id` 一致（`app/` 目前用 `candidate-<interview_id>`）。没给 `candidate_id` 时，不会返回任何候选人资料。 |
| **低置信结果不等于"没有"** | 每条结果的 `metadata` 里有 `confidence` / `low_confidence`。低置信表示"没找到可靠依据"，**不能**理解为"候选人说错了"。目前 `ContextBuilder` 只渲染 `chunk_id/title/content`，低置信结果看起来和高置信一样可信；可以先设 `RAG_DROP_LOW_CONFIDENCE=true` 直接丢掉。 |
| **检索内容是数据，不是指令** | 拼进 prompt 时请当成参考资料包裹（可用 `rag.retrieval.format.format_passages_for_prompt`），不要让模型执行其中的文字。 |

### 常用环境变量

全部可选，完整列表见 `rag/README.md`。

| 变量 | 默认 | 说明 |
|---|---|---|
| `RAG_DATABASE_URL` | 读 `rag/.env` 的 `POSTGRES_*` | 数据库连接。**不会**读取通用的 `DATABASE_URL`，避免误连后端数据库。 |
| `RAG_RERANK` | `false` | 开启重排，每次约多 0.4 秒。 |
| `RAG_DROP_LOW_CONFIDENCE` | `false` | 丢弃低置信结果。 |
| `RAG_STATEMENT_TIMEOUT_MS` | `2500` | 单条 SQL 超时。 |

---

## 3. 目前能检索到什么

| `RetrievalSource` | 状态 |
|---|---|
| `technical` | ✅ 13,068 条：vLLM、Transformers、LangGraph、LlamaIndex、PyTorch、d2l、FastAPI、Django、PostgreSQL、Redis、Kafka、System Design Primer、cp-algorithms、6 篇论文 |
| `question` | ✅ 3,311 条：LeetCode 题库（带难度）、Tech Interview Handbook |
| `candidate` | ⏳ 空：需要接入简历入库 |
| `job` | ⏳ 空：等 SkillsFuture / MyCareersFuture 岗位数据 |
| 评分标准（rubric） | ⏳ 空：等 BARS 建好；契约里也还没有这个 source，见 [`contract-proposals.md`](contract-proposals.md) |

---

## 4. 需要大家一起决定的事

详见 [`contract-proposals.md`](contract-proposals.md)：

1. **`RetrievalResponse` 加正式的置信度字段**：现在只在 `metadata` 里。
2. **`RetrievedChunk` 加 `source_url` / `doc_version`**：方便评分报告引用出处。
3. **评分标准怎么检索**：在 `RetrievalSource` 里加 `RUBRIC`，还是给 Evaluation 单独一个接口。
4. **`RAGRouter._query` 拼的查询太短**（如 `"{topic} {information_goal}"`）：这类查询正是检索最弱的情况（nDCG@10 约 0.21）。建议把候选人原话或回答片段带进查询。

---

## 5. 给 Member 2 自己：怎么导出数据库

```bash
# 导出（自定义压缩格式，约 81 MB）
docker exec intellihire-pg pg_dump -U intellihire -d intellihire \
  -Fc --no-owner --no-privileges > intellihire-rag.dump

# 验证：恢复到临时库，数行数，再删掉
docker exec intellihire-pg createdb -U intellihire restore_check
docker exec -i intellihire-pg pg_restore -U intellihire -d restore_check \
  --no-owner --no-privileges < intellihire-rag.dump
docker exec intellihire-pg psql -U intellihire -d restore_check -Atc "select count(*) from chunks"
docker exec intellihire-pg dropdb -U intellihire restore_check
```

**分发方式**：用组内私下渠道发送，比如网盘、Teams 或微信群文件。**不要**放进 Git，也不要上传到公开的 GitHub Release：库里有第三方文档内容，部分来源的许可不允许公开再分发。数据更新后要重新导出。
