# RAG backlog

Carried over from the standalone `intellihire-data` repository (paths adapted: `data/` is now
`rag/data/`). The first section is new work created by the RAGPort integration.

## RAGPort integration (2026-10)

- [ ] **Team decision: wire the adapter.** `app/` and `backend/` build `InterviewAgentService` without
  `rag=`; no slot exists in `app/adapters`. See `rag/README.md` for the snippet. Note the Agent only
  calls RAG on the non-autonomous question path (`self._question_agent is None`).
- [ ] **Resume ingestion into the `candidate` namespace.** The retrieval side (owner scoping,
  migration 003) is done; nothing writes candidate chunks yet. The `candidate_id` written at ingestion
  must equal `RetrievalRequest.candidate_id` (`app/` currently uses `candidate-<interview_id>`).
  Candidate chunks need their own `sources` row (the FK) and are deleted per source by `rag.cli index`.
- [ ] **Job namespace is empty** (`mycareersfuture-jd` needs the scraper; SkillsFuture xlsx needs a manual download).
- [ ] **Query rewriting for terse router queries** (`"{topic} {information_goal}"`): see
  `rag/docs/contract-proposals.md`. paraphrase nDCG@10 is ~0.21.
- [ ] **Per-call latency budget vs the Agent's 2 s RAG timeout**: measured 230-470 ms warm on MPS (16k rows,
  hybrid, no rerank); CPU-only hosts and rerank are untested. Run `await adapter.warmup()` at start-up.
- [ ] **Low-confidence handling is the caller's job today**: the Agent's `ContextBuilder` renders only
  `chunk_id/title/content`, so `low_confidence` chunks are shown to the question LLM like any other. Options:
  `RAG_DROP_LOW_CONFIDENCE=true` (adapter drops them) or a contract change (proposals doc).
- [ ] **Competency / difficulty vocabulary**: no chunk has a competency label (BARS pending); difficulty exists only as
  LeetCode Easy/Medium/Hard on `question` chunks. Revisit the mapping when BARS labels land.
- [ ] **`RUBRIC` source** is not in `RetrievalSource`; the `evaluator` role (rubric+technical) is unreachable through RAGPort.
- [ ] CI could run `tests/rag/test_index_db.py` and the adapter integration test with a `pgvector/pgvector:pg17`
  service container; today both skip when Postgres is absent.

## Data pipeline and index (carried over)

P0 通过独立验收（报告 `data/reports/20261002-154629.md`，16,379 chunks，178 tests）。
以下为验收时记录的 minor 问题，均不影响使用，后续迭代处理。

## 文档
- [ ] README 目录结构 / 运行说明未同步最新代码（新增 crawler/xlsx loader、residue、e2e 测试、dropped/ 报告）

## 内容残留
- [ ] d2l：1 处 `:eqlabel:`（d2l-en-fb0722406129）；`(**…**)` 强调标记约 78 个 chunk（含 django 63）；少量仅讲 MXNet/Gluon 的段落
- [ ] vllm：mkdocs `!!!` 提示框 325 个 chunk（cp-algorithms 36），`???` / `=== "tab"` 块，`.inc` 文件名做标题（9）
- [ ] cp-algorithms：`src/preview.md` 垃圾 chunk（cp-algorithms-0e4ae5d73917）、meta-refresh 跳转页 —— 加入 exclude
- [ ] pytorch-tutorials：29 个 chunk 有卡片图标残留（`mortar-board;1em;`）；2 个空/错乱代码块（-a78b620ccc7c、-48681c032dff）；rst `\ ` 转义
- [ ] `[image: …]` 占位：transformers 39、cp-algorithms 34、tech-interview-handbook 9、其他若干
- [ ] postgres：23 个 chunk 含零宽字符 U+200B
- [ ] django：9 个 chunk 的 `::` 字面块未转成代码块；querysets 标题含字面 `\s`
- [ ] tech-interview-handbook：表格里的 `&#124;`；7 个标题是带推广链接的 markdown 链接
- [ ] fastapi：内联代码里连续空行被压缩；8 个 chunk > 1200 token（最大 1405）；"Opinions" 推荐语 chunk（fastapi-26efa6c21fc4）价值低，可丢弃；permalink 省略说明数量报告写 1、实际 2，需核对

## 元数据
- [ ] 223 个 chunk 的 `heading_path` 为空（llama_index 83、redis 38、kafka 34、tech-interview-handbook 31、langgraph 23…）
- [ ] kafka-config-reference：6 个 chunk 标题与内容不符；约 97 个 chunk 一条里塞了多个配置项

## 测试
- [ ] e2e 测试补充：token 复算、permalink commit == doc_version、报告数与 JSONL 行数一致
- [ ] e2e 白名单里 leetcode 的 `div`/`b`/`code` 收窄到 "Tag Validator" 那一题

## 待外部输入
- [ ] skillsfuture-ict：需手动从 jobsandskills.skillsfuture.gov.sg 下载 ICT 技能框架 Excel 放到 `data/raw/skillsfuture-ict/`（解析器仅用合成表测试过）
- [ ] mycareersfuture-jd：需组员提供 `mcf_scraper.py`
- [ ] Kaggle 数据集（LinkedIn Job Postings、Resume Dataset）：需 Kaggle 凭证
- [ ] dualoptimization-jobrec：找不到对应仓库，需提供链接
- [ ] bars-competencies、P1–P6 追问模板、合成测试集：团队自建（与 Member 4 共建 BARS）

## 索引与检索层（2026-10-08 更新）
已完成：pg17 + pgvector 0.8.7 上 migrate / index / 测试(239) / eval / 调参；tsvector 定为 simple，`tsv_en` 已删（migration 002）。

- [ ] **人工复核 `rag/data/eval/gold_queries.yaml`（draft）**：优先 g09、g31、g28、g30、g25、g33（见 README 失败分析清单）；复核后重跑 `eval` 和 `eval --tune`，现有调参结论基于未复核的标签
- [ ] paraphrase 类召回差（nDCG 0.21）：试 query 改写 / HyDE，或带 `filters` 的 domain 预路由
- [ ] 精确短语命中被 dense 2× 权重压住（g06 `consistent hashing`）：试"strict-AND 命中则提升 lexical 权重"
- [ ] gold 扩到 100+ 条并留一个 held-out 集，再决定是否继续调 RRF 权重
- [ ] chunking：d2l Dropout 节首 chunk 讲泛化理论而非 dropout 本身（g33）；heading_path 为空的 223 个 chunk 影响 lexical 的 A 权重
- [ ] `competency` 全为 null（等 BARS rubric）；rubric/candidate/job 命名空间还没有数据
- [ ] rerank 目前只重排前 10 且无交叉验证；有更多 gold 后再比较 pool 大小与融合方式

## 2026-10-08 修复轮之后
- [ ] **DB 层权限**：建只读角色 + 行级安全(RLS)或按命名空间的视图，让 agent 代码只拿受限 DSN；现在 ACL 只在应用层，DSN 是超级用户（README 已写明风险）。**未实现**
- [ ] **dense 现在是精确检索（Seq Scan + Sort），是 `hnsw.ef_search=100` 的副作用**（用生产会话设置 EXPLAIN 实测；ef=40 时会走 HNSW）。数据量 ~10× 或改 ef_search/索引参数后复查计划
  （`test_real_index_dense_plan_is_pinned_to_exact_search` 会在计划翻转时大声失败）。若翻转成 HNSW，预期相对精确结果 overlap@10 0.98 / overlap@50 0.95（ef=100，gold 40 条）；
  iterative_scan 在强制 HNSW 的窄过滤下只找回 28/30 个匹配行，届时评估调大 `hnsw.max_scan_tuples`/`ef_search` 或对窄过滤强制精确扫描
- [ ] 置信度阈值（0.52/0.62/0.30/0.80）在 gold+negative 同一批 query 上校准、gold 最低 dense 离阈值只有 0.008：有 held-out query 后重新校准；`pizza`/`lorem ipsum` 这类字面命中暂无办法拦
- [ ] 纯中文 query 没有 lexical 检索（`lexical_hit` 恒 False，置信度最高 medium）；如需要再做 CJK 分词或 trigram on title
- [ ] hybrid 相对 dense 的留出增益 +0.025 [−0.010, +0.059]、rerank 相对 hybrid 的增益都"未证明显著"：gold 扩到 100+ 且经人工复核后重做
- [ ] 标识符切片里 `pg_stat_statements extension` 召回为 0（2× dense 权重压住 lexical-only 命中）
