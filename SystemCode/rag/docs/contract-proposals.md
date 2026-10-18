# Proposed `shared/contracts` changes and router follow-ups

Nothing here is implemented. `shared/contracts/` affects several teams and must change in an isolated
PR; the RAG adapter works against contract version 2.0 as it stands. Each item lists what the adapter
does today so the team can judge whether the change is worth it.

## 1. First-class retrieval confidence

**Proposal.** Add to `RetrievalResponse`:

```python
confidence: Literal["low", "medium", "high"] | None = None
low_confidence: bool = False
```

(Optionally also on `RetrievedChunk`, but the signal is per call, not per chunk.)

**Why.** Retrieval always returns up to `top_k` chunks, even for an off-topic query (a "sourdough"
query returns five chunks). The retriever knows this (`max_dense`, lexical corroboration, optional
rerank score) and reports `low_confidence`, meaning "the corpus has no good evidence", which is
*unverified*, not "false". Today the adapter can only put it in `RetrievedChunk.metadata`, and
`agents/routing/context_builder.py::_render_chunk` renders just `chunk_id`, `title` and `content`, so
the question LLM sees weak chunks as if they were authoritative. With a typed field the Agent can
omit low-confidence chunks, or label them, without parsing a free-form dict.

**Interim.** `RAG_DROP_LOW_CONFIDENCE=true` makes the adapter return no chunks for low-confidence
results; default is to flag and return.

**Compatibility.** Additive with defaults, so no existing producer or consumer breaks; bump
`CONTRACT_VERSION` per the team's convention.

## 2. Provenance on `RetrievedChunk`

**Proposal.** Add `source_url: str | None = None` and `doc_version: str | None = None` (and possibly
`source_name`).

**Why.** Every chunk has an immutable permalink (`source_url`, with commit sha / arXiv `vN` /
dataset revision) and a `doc_version`. The report and the review UI will want to cite where a
technical claim in a question came from. Both are in `metadata` today, which is untyped and easy to
drop silently in a refactor.

**Compatibility.** Additive, optional.

## 3. `RetrievalSource.RUBRIC` or a separate evaluator path

**Problem.** The retrieval layer has a `rubric` namespace and an `evaluator` role (rubric + technical)
for BARS scoring anchors. `RetrievalSource` has only candidate / technical / question / job, so the
evaluation side cannot request rubric chunks through `RAGPort`, and the adapter cannot expose the
`evaluator` role meaningfully (it raises `NamespaceAccessError` for sources that role may not read).

**Options.**

* (a) Add `RUBRIC = "rubric"` to `RetrievalSource`. Simple, but then the question-generation Agent's
  `RAGPort` can also ask for rubric text unless the adapter's role blocks it. The `question_planner`
  role deliberately has no rubric access (so the questioner cannot leak scoring anchors to the
  candidate), and the adapter must keep that.
* (b) Keep `RAGPort` question-side only and give evaluation its own port
  (`EvaluationKnowledgePort`, or a method on the evaluation module's existing ports) that wraps
  `RAGAdapter(role="evaluator")`. Preferred: the Agent cannot request rubric chunks by construction, and
  no enum change is needed.

There is no rubric content in the index yet (BARS is pending), so this is a decision, not a blocker.

## 4. Smaller observations

* `RetrievalRequest.competency` and `difficulty` have no usable counterpart in the corpus yet. No
  chunk has a competency; difficulty exists only as LeetCode Easy/Medium/Hard on `question` chunks, so
  the adapter maps 1-2/3/4-5 onto those tiers for `source=question` only. If the team wants real
  difficulty on technical content, it has to come from labelling, not from the contract.
* `RetrievalRequest.project_id` is not used; chunks carry no project key.
* `partial` is currently set only by `RAGRouter` (another source in the batch failed). A retriever that
  returned fewer than `top_k` because of a *partial* failure inside one source (not the case here) would
  need a separate flag; the adapter does not overload `partial` with low confidence.

## 5. `RAGRouter._query` builds the weakest possible queries

`agents/routing/rag_router.py`:

```python
CANDIDATE: f"{topic} candidate project claims"
QUESTION: f"{topic} {question_type} interview question examples"
JOB: f"{topic} role requirements"
other: f"{topic} {question_plan.information_goal}"  # TECHNICAL
```

These are 3-8 word keyword strings with boilerplate suffixes. On the 40-query draft gold set
(`data/reports/retrieval-eval-20261008-115950.md`, hybrid RRF) nDCG@10 is 0.208 for the 9
paraphrase-style queries (short, no keywords shared with the target passage) versus 0.674 for
conceptual and 0.765 for identifier queries; lexical search is useless on paraphrases (0.042), dense has
to carry them, and 5 of the 9 scored 0. The fixed suffixes also add noise to the lexical side
(`candidate`, `project`, `claims`, `role`, `requirements` are all stop-worthy in a corpus of technical
docs), and `information_goal` often repeats the topic (for example `"kv cache Explain your own
implementation of kv cache"`).

**Suggestions, cheapest first.**

1. **Drop the boilerplate suffixes** for TECHNICAL / QUESTION / JOB (`candidate project claims`, `interview
   question examples`, `role requirements`) and query with the topic plus, when present, the concrete
   claim or answer excerpt the question is about (`question_plan.answer_excerpt` is already on the plan).
   Longer, natural-language queries are what bge-m3 handles best.
2. **Use `candidate_claim`.** The retrieval layer already accepts `RetrievalIntent.candidate_claim`
   and appends it to the dense query text only (the lexical side stays on `query`). The contract has no
   field for it; either add `RetrievalRequest.claim_text: str | None` (item 6) or let the router fold the
   claim into `query`.
3. **Pass `domain`** only when it is a corpus domain; the adapter already normalises and validates it, so
   the router can pass the project's domain verbatim.
4. **Query rewriting / HyDE** (one short LLM call producing 1-2 paraphrases, or a hypothetical answer
   to embed), measured on a larger gold set before adoption. It is on `rag/BACKLOG.md`. It belongs behind
   the adapter or in the router, never in the Agent's policy code; the router is the right owner because
   it already decides what to ask.
5. Evaluate any change with `rag/eval` (`python -m rag.cli eval`) before and after; the gold set needs
   to grow to 100+ reviewed queries to make differences of +/-0.03 meaningful.

## 6. Optional: `RetrievalRequest.claim_text`

A nullable `claim_text: str | None` (candidate statement under discussion) would let the adapter map it
to `candidate_claim` without folding it into `query`, keeping lexical search precise while enriching the
dense query. Only worth adding together with suggestion 5.2.
