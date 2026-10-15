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
  hybrid, no rerank); rerank is untested on CPU. CPU-only with the int8 ONNX encoder on Apple arm64: encode
  13 ms, hybrid retrieve 78-150 ms p50 (see the ONNX section below); x86 is still unmeasured. Run
  `await adapter.warmup()` at start-up.
- [ ] **Low-confidence handling is the caller's job today**: the Agent's `ContextBuilder` renders only
  `chunk_id/title/content`, so `low_confidence` chunks are shown to the question LLM like any other. Options:
  `RAG_DROP_LOW_CONFIDENCE=true` (adapter drops them) or a contract change (proposals doc).
- [ ] **Competency / difficulty vocabulary**: no chunk has a competency label (BARS pending); difficulty exists only as
  LeetCode Easy/Medium/Hard on `question` chunks. Revisit the mapping when BARS labels land.
- [ ] **`RUBRIC` source** is not in `RetrievalSource`; the `evaluator` role (rubric+technical) is unreachable through RAGPort.
- [ ] CI could run `tests/rag/test_index_db.py` and the adapter integration test with a `pgvector/pgvector:pg17`
  service container; today both skip when Postgres is absent.

## ONNX int8 query encoder for the 2-vCPU / 3.5 GB server (2026-10-15)

Done: `RAG_ENCODER=onnx-int8` (onnxruntime + tokenizers, no torch), build script and committed manifest,
`index_meta` compatibility rules, `requirements-serve.txt`, tests, measurements
(`data/reports/encoder-onnx-int8-20261015.md`), server section in `docs/TEAM_INTEGRATION.md`.
Also fixed on the way: `rag.cli eval` (default, `--tune`, `--ident-study`) had crashed on `main` since the
candidate-isolation change (`tune.collect` built a filter that still contained the candidate namespace).

- [ ] **Deploy owner (Member 5): everything in `docs/TEAM_INTEGRATION.md` section 6.4**: add `psycopg-pool` and
  `pgvector` to `deploy/requirements-linux.lock.txt`, `RAG_*` variables in `app.env`, copy the model directory,
  confirm the 4 GB swap that is already enabled (no second swapfile), a pgvector-enabled Postgres, run `python -m rag.index.encoder_commands verify --smoke` on the server.
  Nothing under `deploy/` or `.github/` was touched here.
- [ ] **Measure on real x86.** Everything timed here is Apple arm64; the amd64 container ran under qemu (latency
  100x pessimistic, only memory and cosine are meaningful). Needed: encode p50/p95 on the 2 vCPU box with the app
  running. The default `uint8-pc` is u8u8, whose x86 latency is **unmeasured**. Without VNNI the u8s8 recipes can
  saturate; `--smoke` checks accuracy on the server CPU. Fallbacks: `build-onnx --variant int8-pt`, last resort
  `int8-pc-rr` (nDCG -0.020).
- [ ] **Tokenizer memory.** `tokenizers` needs about 0.39 GB for the XLM-R vocabulary (half of the 0.78 GB process).
  `sentencepiece` needs about 0.07 GB and gave identical ids on the 98 test queries, but it is not in the deploy lock;
  adopt it (with a parity test over a larger query sample) only if the box gets tight.
- [ ] **int8 quality on the weak categories.** For the default `uint8-pc`: paraphrase hybrid nDCG +0.017
  [-0.012, +0.064] (p=1.0, n=9), identifier slice 0.640 to 0.623 (-0.017 [-0.036, -0.003], exact p=0.062, n=10). The
  first default `int8-pc` had paraphrase -0.057 [-0.131, -0.003] (p=0.25: two queries drive it). 7 recipes were picked
  on the same 40 draft queries, so the winner's numbers are optimistic. Re-check on the enlarged, human-reviewed gold
  set. Thresholds 0.52 / 0.62 were not retuned (gold g18 moves high to medium).
- [ ] **Artifact distribution.** The int8 files are copied by hand (rsync/scp). Publishing them to a private bucket or
  a private HF repo, and downloading with the sha256 from the manifest, would remove the manual step.
- [ ] fp16 ONNX variant was not built (onnxruntime's CPU provider has no native fp16 kernels, it would be slower and
  1.1 GB); revisit only for GPU hosts.
- [ ] `python -m rag.cli` imports the whole ingestion pipeline (requests, tiktoken, datasets) at start-up, so serve-only
  installs use `python -m rag.index.encoder_commands`. Make the `rag.cli` imports lazy.
- [ ] Dev `docker-compose.yml` runs Postgres with default settings (`shared_buffers=128MB`): hybrid p50 was 150 ms
  against 78 ms on a pg14 container with `shared_buffers=256MB` (different major version, not a controlled test).
  Check whether the dev default should be raised.

## Corpus expansion, batch 1 (2026-10, branch `feat/rag-corpus-expansion`)

Batch 1 added 93 technical-documentation sources (frontend, backend/web frameworks and databases,
DevOps/SRE/cloud, data engineering, classic ML / data science, security): 47,871 chunks. Per-source
licence, scope and counts: `data/sources.yaml` and the Corpus section of `README.md`. Open items:

- [ ] **Held-out coverage is unmeasured.** The blind auditor's score on the held-out queries is the real
  number; the dev self-check (57 of 66 batch-1 dev queries, up from 16) is indicative only. Re-run the
  coverage audit after merge.
- [ ] **Paraphrase queries are still the weak spot.** Two dev paraphrases miss although the content is in the
  index (MDN "Debounce" for "stopping a search box from firing an API call on every keystroke",
  Wikipedia "Infrastructure as code" for "describing servers in files..."). gold eval paraphrase nDCG@10 is
  0.17. Query rewriting (see `contract-proposals.md`) is the lever, not more content.
- [ ] **Batch 2: remaining domains** (mobile, algorithms/CS, deep learning research, LLM/agents,
  embedded, AI infra, test/QA, game dev, CV/video). Registered-but-not-ingested P1/P2 sources still
  exist for several of these (swift-book, kotlin-docs, flutter-website, react-native-website,
  playwright-docs, pytest-docs, godot-docs, zephyr-docs, mcp-spec, ...). The `wikipedia` loader takes
  a title list per domain.
- [ ] **Interview Q&A banks are deliberately not ingested** (`front-end-interview-handbook`,
  `devops-exercises`, `data-science-interviews`, `ml-interviews-*`, `android-interview-questions` are
  `reference_only: true` with a note). The team generates questions with an LLM grounded in the
  technical chunks. devops-exercises has no licence in the repo; the ML interview books are
  all-rights-reserved or unspecified.
- [ ] **Licence follow-ups.** `mongodb-docs` (no licence file; site licence not confirmed),
  `elastic-docs-content` (CC BY-NC-ND), `mysql-innodb-docs` (Oracle proprietary), `terraform-docs`
  (BUSL), `google-sre-book` / `bytebytego-system-design-101` (ND), `hudi-docs` (user docs repo not
  located), javascript.info (permission required, not ingested), fpp3 (text not in the repo).
  `owasp-community` rests on owasp.org's site-wide CC BY-SA 4.0 statement (the repo has no LICENSE
  file); `networks-systems-approach`'s README says derivative works are "by arrangement" although
  its LICENSE is CC BY 4.0: confirm with the authors if the dump is ever shared outside the team.
  `clickhouse-docs`, `interpretable-ml-book`, `think-*` and `linux-insides` are NC (private use
  only); `ansible-docs` is GPL-3.0 and `grafana-docs` AGPL-3.0 (copyleft). `web-dev` is an archived
  repo (CC BY 3.0).
- [ ] **Confidence rule not recalibrated** for the larger corpus: 16/22 negatives flagged low (18/22
  before, after replacing four negatives that now have legitimate literal matches). Re-fit the
  thresholds on a held-out negative set.
- [ ] **HNSW index is unused** (exact dense search is the default). It is still built and maintained;
  drop it or rebuild it (`REINDEX` with `maintenance_work_mem=1GB`, `ef_search>=400`) when the corpus
  approaches ~300k rows. An incrementally built index was measurably worse than a rebuilt one.
- [ ] **AsciiDoc sources lose code that lives in separate files** (`include-code::` / `include::`
  in Spring Framework / Boot / Security / Data JPA docs): prose and inline code survive, many
  examples do not. Resolving Antora `example$` targets would restore them.
- [ ] **Quarto R chunks are dropped** (openintro-ims, interpretable-ml-book): prose and equations only.
- [ ] **Wikipedia extracts**: formulas are re-joined from the MathML fallback heuristically (inline
  `$...$`); long derivations can still read oddly. Articles were fetched once (revision permalinks
  pin them); re-run `all --source wikipedia-*` to refresh.
- [ ] **Known residue**: a few MDN pages whose blockquoted fences defeat fence detection and
  Wikipedia pages that show HTML in plain text are whitelisted in `test_chunks_e2e.py`
  (`LEGIT_HTML_IN_PROSE`); long unwrapped paragraphs in cnp3, Wikipedia and Debezium tables
  (`long_line` in `rag.residue`).
- [ ] **Chunks without a heading path** (page intro before the first heading) remain for sources whose
  title is not in frontmatter; `options.frontmatter_title` fixes the rest.
- [ ] **`rag.cli eval` crashed in its held-out step** on `main` (candidate namespace without
  `candidate_id` in `rag/eval/tune.py`); fixed in this branch by scoping namespaces like the retriever.

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
