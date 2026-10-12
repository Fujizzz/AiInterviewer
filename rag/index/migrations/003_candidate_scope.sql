-- Candidate isolation (security-critical). Candidate-namespace chunks (resume content) must be
-- retrievable only for the candidate they belong to, so ownership becomes a first-class,
-- indexed column instead of a free-form metadata key.
--
--   * candidate_id is the caller-supplied opaque candidate identifier (RetrievalRequest.candidate_id).
--   * The CHECK makes the invariant a database fact: a 'candidate' chunk MUST carry an owner and
--     no other namespace may carry one. Existing rows (technical/question only) already satisfy it.
--   * Retrieval filters on it in SQL (rag/retrieval/sql.py) and re-checks it on every returned row.
ALTER TABLE chunks ADD COLUMN IF NOT EXISTS candidate_id text;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chunks_candidate_scope_chk') THEN
        ALTER TABLE chunks ADD CONSTRAINT chunks_candidate_scope_chk
            CHECK ((namespace = 'candidate') = (candidate_id IS NOT NULL AND candidate_id <> ''));
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS chunks_candidate_idx ON chunks (candidate_id) WHERE candidate_id IS NOT NULL;
