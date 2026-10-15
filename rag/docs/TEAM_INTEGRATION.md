# RAG 模块接入指南（给组员）

RAG 模块实现了 Agent 的 `RAGPort` 接口（`agents/ports/rag.py`）。接入后，Agent 出题时会自动检索技术文档和题库；不接入时，Agent 照常运行，只是没有检索。

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

`InterviewAgentService` 已经支持 `rag=` 参数，只是现在两处构造都没传：

- `app/application.py`
- `backend/interviews/agent_session.py`

在**进程启动时**加：

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
| **检索只在非自主出题路径上被调用** | 读代码看到，Agent 只在 `question_agent is None` 时走 `RAGRouter`。如果默认用的是 ReAct 自主出题路径，传了 `rag=` 也可能不触发检索——需要负责 Agent 的组员确认。 |
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

---

## 6. 部署到 2 vCPU / 3.5 GB 的服务器（给负责 `deploy/` 的组员）

目标机器：阿里云 ECS，Ubuntu 22.04，**x86_64，2 vCPU，内存 3.5 GB（可用约 2.3 GB）**，应用已在跑
（`/opt/ai-interviewer`，Python 3.12，依赖来自 `deploy/requirements-linux.lock.txt`）。
**服务器现已启用 4 GB swap**（`/swapfile`，`vm.swappiness=10`，由负责人配置）；它只是 OOM 保险，下面的内存预算仍按不用 swap 来算。

**不能用默认的 torch 编码器**：torch + fp32 的 bge-m3 常驻约 2–3 GB（实测 CPU fp32 仅编码器就 2.05 GB），会把应用一起 OOM-kill。
服务器上用 `RAG_ENCODER=onnx-int8`：同一个 bge-m3，动态量化成 int8，只依赖 `onnxruntime` + `tokenizers`，
不 import torch / transformers / sentence-transformers（`tests/rag/test_onnx_no_torch.py` 强制检查）。
语料向量不用重做：库里仍是 torch 版 bge-m3 的向量，只是查询向量由 int8 版算出
（和 torch 版的余弦约 0.989；在 40 条 draft 查询上总体检索指标与 torch 无显著差异，但标识符类查询略降，且样本很小，
数字和统计说明见 [`rag/README.md`](../README.md) 和 `rag/data/reports/encoder-onnx-int8-20261015.md`）。

### 6.1 环境变量（写进 `/etc/ai-interviewer/app.env`，0600）

| 变量 | 服务器上的值 | 说明 |
|---|---|---|
| `RAG_ENCODER` | `onnx-int8` | 默认 `torch`，必须显式改。 |
| `RAG_ENCODER_MODEL_DIR` | `/var/lib/ai-interviewer/models/bge-m3-onnx-int8` | 模型目录。**不要放在 `/opt/ai-interviewer/releases/<sha>/` 下**：每次发布换目录，模型会跟着丢。和已有的 `answer-completion` 模型同一个根目录，`ReadWritePaths=/var/lib/ai-interviewer` 已覆盖。 |
| `RAG_ENCODER_THREADS` | `2`（默认） | onnxruntime `intra_op_num_threads`。只有 2 个核，和应用抢；想把 RAG 对应用的影响压低可设 `1`（单次编码约慢 50%）。 |
| `RAG_ENCODER_VERIFY` | `true`（默认） | 加载时校验模型文件 sha256（读 570 MB，约 1–2 秒）。**设成 `false` 只检查文件大小**：大小相同的损坏文件会悄悄通过，除非启动时间有硬性要求，请保持 `true`。 |
| `RAG_DATABASE_URL` | `postgresql://…@127.0.0.1:<端口>/intellihire` | **必须显式设置。** `app.env` 里已经有 `POSTGRES_*`（指向应用库 `ai_interviewer`），而 RAG 在没设 `RAG_DATABASE_URL` 时会回退读 `POSTGRES_*`，会连到应用库。 |
| `RAG_RERANK` | `false`（默认） | **服务器上禁用重排**：cross-encoder 要 torch + 再 2.3 GB，放不下。`RAG_ENCODER=onnx-int8` 且 `RAG_RERANK=true` 时 `create_rag_adapter()` 会直接抛 `RAGUnavailableError`。 |
| `RAG_POOL_MAX` | `2`–`4` | 小机器上别开太大；每个连接都是一个 Postgres 进程。 |

### 6.2 模型文件怎么上服务器

在开发机构建（需要 `onnx`、约 4 GB 空闲内存、联网下载官方 ONNX 约 2.3 GB，构建本身约 30 秒）：

```bash
uv pip install -r rag/requirements.txt
uv run python -m rag.cli encoder build-onnx          # 默认 uint8-pc -> rag/data/models/bge-m3-onnx-int8/ (gitignored, ~590 MB)
uv run python -m rag.cli encoder verify              # 对照 rag/data/encoder_manifest.json 校验 sha256
```

**不要在服务器上量化**：量化时要把 2.3 GB 的 fp32 图读进内存，整个 `build-onnx` 命令峰值实测约 3.5 GB RSS
（只量化那一步的探针曾测到 5.2 GB），服务器可用内存只有约 2.3 GB，会 OOM。
只拷贝成品（3 个文件：`model.onnx` 2 MB、`model.onnx.data` 568 MB、`tokenizer.json` 17 MB）：

```bash
rsync -avP rag/data/models/bge-m3-onnx-int8/{model.onnx,model.onnx.data,tokenizer.json} \
    root@<server>:/var/lib/ai-interviewer/models/bge-m3-onnx-int8/
# 服务器上
chown -R aiinterviewer:aiinterviewer /var/lib/ai-interviewer/models/bge-m3-onnx-int8
chmod 0750 /var/lib/ai-interviewer/models/bge-m3-onnx-int8; chmod 0640 /var/lib/ai-interviewer/models/bge-m3-onnx-int8/*
```

模型不在 Git 里，也没有发布到任何公开位置（官方只有 fp32 ONNX，没有 int8；本次也没有上传任何东西）。manifest
`rag/data/encoder_manifest.json`（已提交）记录了成品每个文件的 sha256、基础模型和 revision、量化参数；
加载时会校验，文件被截断或换成别的构建会直接报错而不是悄悄用错模型。
`model.onnx.data` 必须和 `model.onnx` 放在同一目录（权重放在外部文件里，onnxruntime 用 mmap 读取，
常驻内存比单文件格式小约 1 GB，而且这些页面是可回收的 page cache）。

### 6.3 依赖：`rag/requirements-serve.txt`

只含服务检索需要的包（x86 Linux、Python 3.12）：`numpy`、`onnxruntime`、`tokenizers`、`psycopg[binary]`、
`pydantic`（都已在 lock 里，**版本与 lock 完全一致**）加上 lock 里**没有**的两个：

| 包 | 版本（已测） | 说明 |
|---|---|---|
| `psycopg-pool` | 3.3.3（`>=3.2,<4`） | 连接池 |
| `pgvector` | 0.5.1（`>=0.3,<0.6`） | psycopg 的 vector 类型适配，依赖 numpy |

和 lock 的核对结果（在 `python:3.12-slim` linux/amd64 里实测，`pip check` 通过）：`numpy==2.0.2`、
`psycopg==3.3.3`、`psycopg-binary==3.3.3`、`pydantic==2.13.5`、`onnxruntime==1.23.2`、`tokenizers==0.22.2` 与 lock
逐字一致，没有冲突，也不会升降任何已锁版本；`tokenizers` 会顺带要求 `huggingface-hub`，lock 里已有 `1.33.0`。
**不要**把 `rag/requirements.txt` 装进服务器环境：它固定 `huggingface-hub<1`（与 lock 的 1.33.0 冲突）并包含 torch。

### 6.4 你需要做的事（`deploy/` 和 `.github/` 不是 Member 2 的目录，这里只列清单，没有改动）

1. 在 `deploy/requirements-linux.lock.txt` 里加 `psycopg-pool==3.3.3` 和 `pgvector==0.5.1`（其余已在 lock 里）。
   注意 deploy workflow 的 verify job 是 `pip install --no-deps -r` 这个 lock 再 `python -m pytest -q`：
   这个环境**没有** pgvector / psycopg_pool，所以测试里涉及它们的模块都用了 `pytest.importorskip`
   （已在按 workflow 方式装的 python3.12 环境里跑过全量：0 error）；加进 lock 以后这些测试会开始真的运行
   （需要 Postgres 或模型文件的会自己 skip）。
2. 把 `RAG_*` 变量写进 `/etc/ai-interviewer/app.env`（见 6.1），把 `deploy/app.env.example` 补上同样的注释行。
3. 部署流程里增加「模型目录存在且 sha256 对」的检查，例如发布后跑
   ```bash
   cd /opt/ai-interviewer/current
   RAG_ENCODER_MODEL_DIR=/var/lib/ai-interviewer/models/bge-m3-onnx-int8 \
     .venv/bin/python -m rag.index.encoder_commands verify --smoke
   ```
   `verify` 的目录优先级：`--dir`，其次环境变量 `RAG_ENCODER_MODEL_DIR`（和应用用的是同一个变量），最后才是仓库内默认路径；
   所以环境变量与 `app.env` 一致即可，不必再写 `--dir`。服务器环境里用 `rag.index.encoder_commands` 入口，不是 `rag.cli`：
   `rag.cli` 启动时会 import 入库流水线的 requests/tiktoken 等，serve 依赖里没有。
   它在**服务器 CPU 上**加载模型，对比仓库里提交的 torch 参考向量（余弦均值须 ≥ 0.97、单条 ≥ 0.95），同时打印单次编码耗时和峰值内存；
   退出码 0 才算通过。`--smoke` 是为了发现 x86 上 int8 内核问题：没有 VNNI 的 AVX2 CPU 上 u8s8 乘积可能饱和、精度下降
   （onnxruntime 量化文档「When and why do I need to try U8U8?」）；默认的 `uint8-pc` 是 u8u8，不走这条路径，
   但**x86 上 u8u8 的耗时没有实测过**（开发机是 arm64，容器是模拟的）。若检查不通过，依次改用
   `python -m rag.cli encoder build-onnx --variant int8-pt --update-manifest`，最后才是 `--variant int8-pc-rr`
   （7 位权重，实测更差：nDCG -0.020），在开发机重新构建、提交 manifest 并重新拷贝。
4. **确认 swap 已存在，不要再建第二个 swapfile**：`swapon --show` 应看到 `/swapfile`（4G），`cat /proc/sys/vm/swappiness` 为 10。
   swap 只是 OOM 保险，不是常态内存；PDF 沙箱、Celery worker 的尖峰仍可能让整机变慢。
5. Postgres 见 6.5。向量库要有 pgvector，且是 `RAG_DATABASE_URL` 指向的**独立数据库**。
6. 把 `create_rag_adapter()` / `warmup()` 接进应用（第 2 节），`warmup()` 在启动时加载模型，之后才接收请求。

### 6.5 Postgres（小机器）

服务器现有的是 **PostgreSQL 14（apt，监听 5432，应用库 `ai_interviewer`）**，RAG 需要 pgvector（`CREATE EXTENSION vector`，
需要 superuser；HNSW 索引需要 pgvector ≥ 0.5，代码会检测并跳过 `iterative_scan`，但请装 ≥ 0.8）。两种做法：

* **A（推荐，隔离）**：用本目录的 docker compose 起一个独立的 `pgvector/pgvector:pg17`，绑 `127.0.0.1:5433`，
  不碰应用库。小机器用 `command:` 覆盖参数，写成服务器上的 `docker-compose.override.yml`（未在真机上试过；
  同样的参数已在 `pgvector/pgvector:pg14`、`--memory 700m --cpus 1` 的容器里验证过导入和检索）：
  ```yaml
  services:
    db:
      ports: ["127.0.0.1:5433:5432"]
      mem_limit: 700m
      shm_size: 256mb
      command: >
        postgres -c shared_buffers=256MB -c effective_cache_size=640MB -c work_mem=4MB
        -c maintenance_work_mem=64MB -c max_connections=20 -c wal_buffers=4MB -c max_wal_size=512MB
  ```
  （服务器是否装有 Docker，`deploy/README.md` 没写；没有的话用 B。）
* **B（不装 Docker，用现有 PG14）**：
  ```bash
  # 1) PGDG apt 源（jammy-pgdg；Ubuntu 自带源里的 pgvector 版本偏旧）
  install -d /usr/share/postgresql-common/pgdg
  curl -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc --fail https://www.postgresql.org/media/keys/ACCC4CF8.asc
  echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt jammy-pgdg main" > /etc/apt/sources.list.d/pgdg.list
  apt update && apt install postgresql-14-pgvector     # 负责人确认 jammy-pgdg 上有 0.8.7；本机未验证，装完看 pg_available_extensions
  # 2) 独立数据库和角色（不要用应用库 ai_interviewer）
  sudo -u postgres createuser intellihire && sudo -u postgres psql -c "ALTER ROLE intellihire PASSWORD '...'"
  sudo -u postgres createdb -O intellihire intellihire
  sudo -u postgres psql -d intellihire -c 'CREATE EXTENSION vector'      # 需要 superuser
  sudo -u postgres psql -d intellihire -c '\dx vector'                  # 确认版本
  ```
  然后按下面「导入数据」导入，`RAG_DATABASE_URL` 指向 `127.0.0.1:5432/intellihire`。
  **小机器内存参数写进 `postgresql.conf` 是整个集群共享的，会同时影响应用库 `ai_interviewer`**（`shared_buffers` 需要重启，应用会短暂断库）：
  先 `SHOW shared_buffers; SHOW work_mem;` 看现值，再按需调到 `shared_buffers = 256MB`、`effective_cache_size = 640MB`、`work_mem = 4MB`、
  `maintenance_work_mem = 64MB`（只在导入建 HNSW 时需要，之后可调回）。`work_mem` 等可以用
  `ALTER DATABASE intellihire SET work_mem = '4MB'` 只作用于 RAG 库；`shared_buffers` 没有按库设置的办法。
  这些数值是在 pg14 小容器里验证的，不是在共享集群上验证的；`shared_buffers` 多占的 128 MB 要算进上面的内存预算。

**要不要加进 `rag/docker-compose.yml`？不加。** 那个文件是开发机用的（默认值、`pg17`、5432），开发机内存充足；
服务器参数放在服务器侧的 override 里（上面），避免两边互相影响。（附带发现：开发容器用默认 `shared_buffers=128MB`，
混合检索 p50 150 ms，而 256MB 的 pg14 小容器是 78 ms——主版本不同，不是对照实验，记在 BACKLOG。）

**导入数据要注意版本**：`intellihire-rag.dump` 是 PG17 的 `pg_dump -Fc` 产物，**PG14 的 `pg_restore` 读不了**
（实测：`pg_restore: error: unsupported version (1.16) in file header`）。方案 A（PG17 容器）直接 `pg_restore` 即可；
方案 B 需要先用 PG17 客户端转成纯 SQL，再用 psql 导入 PG14（实测可行：16,379 行和 12 个索引都恢复，之后 `rag.cli migrate`
报 `schema already current`；唯一的报错是 PG17 才有的 `SET transaction_timeout`，可忽略）：

```bash
docker exec -i intellihire-pg pg_restore --no-owner --no-privileges -f - < intellihire-rag.dump > intellihire.sql
psql "$RAG_DATABASE_URL" -v ON_ERROR_STOP=0 -q < intellihire.sql
```

参数依据：库约 300–330 MB（表 151 MB + HNSW 128 MB），稠密检索现在是精确扫描（README「Known limitations」），
热数据约 150 MB，所以 `shared_buffers=256MB` 够用；`work_mem` 只给 4 MB，连接数 ≤ 20（池最大 4）。
`maintenance_work_mem=64MB` 只影响建索引（导入时），16k 行 HNSW 在 64 MB 下能建完（实测）。
该容器在导入并跑完 100 次检索后占用 291 MiB（上限 700 MiB）。

### 6.6 预期占用

检索进程（含 psycopg 池、encoder、tokenizer）**约 0.8 GB RSS**（Apple arm64 实测 0.78 GB；amd64 容器 0.76–0.80 GB；
其中 `tokenizers` 的 XLM-R 词表约 0.39 GB，模型 mmap 约 0.34 GB）。对「可用约 2.3 GB」是可承受的，再加 Postgres
（容器上限 700 MB，实测 291 MiB）仍有余量；torch 版则 > 2 GB（CPU fp32 仅编码器实测 2.05 GB 稳态；MPS 开发默认的瞬时峰值在不同次运行里是 2.3–3.8 GB，稳态 2.1 GB）。权重是可回收的 page cache，实测在 512 MB 的 cgroup 限制下也能跑完。
延迟：arm64 单次编码约 13 ms（2 线程）、端到端检索约 80–150 ms；x86 **未实测**（只有 qemu 模拟，慢 100 倍以上，不能当数据），
粗略按算力估计在 2 核 AVX2/VNNI 上每次几十到一百多毫秒，Agent 的 2 s 超时有很大余量——**请在服务器上跑
`rag.index.encoder_commands verify --smoke` 得到真实数字**。

### 6.7 回退

在服务器上没有「切回 torch」这个选项（没有 torch，也放不下）；出问题时的回退是不接 RAG：`create_rag_adapter()` 抛
`RAGUnavailableError` 时 Agent 本来就无检索运行（第 2 节）。
