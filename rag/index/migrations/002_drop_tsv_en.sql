-- 2026-10-08: lexical config decision = 'simple' (see README "tsvector 配置选择" and
-- data/reports/retrieval-eval-20261008-102646.md / retrieval-tune-20261008-102824.md).
-- The experimental 'english' column and its index are removed.
DROP INDEX IF EXISTS chunks_tsv_en_gin;
ALTER TABLE chunks DROP COLUMN IF EXISTS tsv_en;
