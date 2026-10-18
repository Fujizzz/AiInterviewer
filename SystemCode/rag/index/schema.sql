-- IntelliHire RAG store. Idempotent: safe to re-run (python -m rag.cli migrate).
-- Target: PostgreSQL 17 + pgvector >= 0.7 (pgvector/pgvector:pg17 image).

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE IF NOT EXISTS schema_migrations (
    version     integer PRIMARY KEY,
    sha256      text NOT NULL,
    applied_at  timestamptz NOT NULL DEFAULT now()
);

-- Key/value facts about the vectors currently stored (model, revision, format, dim).
-- The loader and the retriever both refuse to run when their embedder disagrees.
CREATE TABLE IF NOT EXISTS index_meta (
    key         text PRIMARY KEY,
    value       text NOT NULL,
    updated_at  timestamptz NOT NULL DEFAULT now()
);

-- Mirror of data/sources.yaml (one row per registered source).
CREATE TABLE IF NOT EXISTS sources (
    name                  text PRIMARY KEY,
    namespace             text NOT NULL,
    priority              text,
    url                   text,
    loader                text,
    license               text,
    auto_level            text,
    trust_level           text,
    refresh_cycle         text,
    domain                text,
    related_technologies  text[] NOT NULL DEFAULT '{}',
    reference_only        boolean NOT NULL DEFAULT false,
    verified              boolean NOT NULL DEFAULT true,
    clean_profile         text,
    notes                 text,
    options               jsonb NOT NULL DEFAULT '{}'::jsonb,
    synced_at             timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT sources_namespace_chk CHECK
        (namespace IN ('candidate','job','technical','rubric','question','test'))
);

-- text[] -> text, declared IMMUTABLE so it can appear in a generated column
-- (array_to_string is only STABLE in core; for text[] it is safe).
CREATE OR REPLACE FUNCTION rag_join_path(p text[]) RETURNS text
    LANGUAGE sql IMMUTABLE PARALLEL SAFE
    AS $$ SELECT array_to_string(p, ' ') $$;

-- lower-cased copy of a text[] (tags are mixed-case: 'Graph', 'vllm'); IMMUTABLE for indexing.
CREATE OR REPLACE FUNCTION rag_lower_arr(p text[]) RETURNS text[]
    LANGUAGE sql IMMUTABLE PARALLEL SAFE
    AS $$ SELECT coalesce(array_agg(lower(x)), '{}'::text[]) FROM unnest(p) AS x $$;

CREATE TABLE IF NOT EXISTS chunks (
    chunk_id              text PRIMARY KEY,
    namespace             text NOT NULL,
    text                  text NOT NULL,
    title                 text,
    heading_path          text[] NOT NULL DEFAULT '{}',
    source_name           text NOT NULL REFERENCES sources(name),
    source_type           text NOT NULL,
    source_url            text NOT NULL,
    doc_version           text,
    domain                text,
    topic                 text,
    related_technologies  text[] NOT NULL DEFAULT '{}',
    competency            text,
    difficulty            text,
    trust_level           text NOT NULL,
    license               text NOT NULL,
    token_count           integer NOT NULL,
    ingested_at           timestamptz,
    metadata              jsonb NOT NULL DEFAULT '{}'::jsonb,

    -- sha256 of the exact embedding input string (see rag.index.embed.build_embedding_text)
    text_hash             text NOT NULL,
    embedding_model       text NOT NULL,
    embedding             vector(1024) NOT NULL,

    -- Lexical column: 'simple' config (no stemming, no stop words, so tech identifiers survive;
    -- chosen over english on eval evidence; migration 002 removed the experimental tsv_en.
    -- title+heading weigh A, body weighs B.
    tsv                   tsvector GENERATED ALWAYS AS (
        setweight(to_tsvector('simple', coalesce(title, '') || ' ' || rag_join_path(heading_path)), 'A') ||
        setweight(to_tsvector('simple', text), 'B')
    ) STORED,

    CONSTRAINT chunks_namespace_chk CHECK
        (namespace IN ('candidate','job','technical','rubric','question','test'))
);

-- Dense: HNSW, cosine (embeddings are L2-normalised, <=> is cosine distance).
CREATE INDEX IF NOT EXISTS chunks_embedding_hnsw
    ON chunks USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);

-- Lexical.
CREATE INDEX IF NOT EXISTS chunks_tsv_gin    ON chunks USING gin (tsv);
-- Exact identifiers (select_related, KV-cache, env var names): trigram substring match.
CREATE INDEX IF NOT EXISTS chunks_text_trgm  ON chunks USING gin (text gin_trgm_ops);

-- Filters.
CREATE INDEX IF NOT EXISTS chunks_namespace_idx   ON chunks (namespace);
CREATE INDEX IF NOT EXISTS chunks_source_idx      ON chunks (source_name);
CREATE INDEX IF NOT EXISTS chunks_domain_idx      ON chunks (domain);
CREATE INDEX IF NOT EXISTS chunks_difficulty_idx  ON chunks (difficulty);
CREATE INDEX IF NOT EXISTS chunks_competency_idx  ON chunks (competency);
CREATE INDEX IF NOT EXISTS chunks_reltech_gin     ON chunks USING gin (rag_lower_arr(related_technologies));
CREATE INDEX IF NOT EXISTS chunks_metadata_gin    ON chunks USING gin (metadata jsonb_path_ops);

-- Candidate PII lives here, NOT in chunks. Candidate-namespace chunks must reference a
-- candidate only through an opaque id (metadata->>'candidate_ref'), so retrieval,
-- evaluation and LLM prompts can run blind. Empty until the resume pipeline exists.
CREATE TABLE IF NOT EXISTS candidate_identity (
    candidate_ref  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    full_name      text,
    email          text,
    phone          text,
    extra          jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at     timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE candidate_identity IS
    'PII for candidates, kept apart from chunks so evaluation can be blind. '
    'Never join this table in retrieval or evaluation code paths.';
