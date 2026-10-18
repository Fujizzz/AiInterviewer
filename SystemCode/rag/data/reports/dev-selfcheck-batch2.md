# Dev self-check, batch 2 (68 queries from queries_dev.yaml, new domains only)

Strict criterion: top-5 (hybrid, k=5, no rerank) holds substantive material on the specific concept, not a name-drop
or a different concept sharing a word. Judged by hand by the implementer; indicative only (dev set, not the held-out audit).

| domain | before | after |
|---|---|---|
| mobile (11) | 0 | 9 |
| embedded_systems (11) | 1 | 9 |
| game_dev (10) | 0 | 9 |
| cv_video (9) | 3 | 9 |
| test_qa (8) | 0 | 7 |
| deep_learning_research (10) | 7 | 9 |
| llm_agents (9) | 8 | 8 |
| total (68) | 19 (28%) | 60 (88%) |

Misses after: q037 SwiftUI @State/@Binding (only Swift Evolution Observation/property wrappers; SwiftUI docs are proprietary),
q046 and q193 (Chinese queries; English content exists), q195 button debounce (Wikipedia "Switch > Debouncing" exists, only the paraphrase misses),
q226 regression paraphrase (Wikipedia "Regression testing" exists), q238 Unity router query (netcode content exists, product name dominates),
q093 ViT/DDP router query, q109 LangChain/FAISS RAG chatbot router query.
Passes judged weak: q049 (Core Data/SQLite/Realm: three separate articles, no comparison), q116 (vLLM KV-cache pages, no general explainer).
