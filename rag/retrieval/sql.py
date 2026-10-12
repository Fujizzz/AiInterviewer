"""SQL construction for retrieval. Pure functions (no DB) so they can be unit-tested.

All user-controlled values travel as bind parameters; only whitelisted identifiers
(the lexical column) are interpolated into SQL text.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from .access import CANDIDATE_NAMESPACE
from .intent import RetrievalIntent

# Only the weighted 'simple' column exists. An 'english' column was evaluated and dropped (migration
# 002).
LEXICAL_COLUMNS = {"simple": "tsv"}

# 'simple' keeps stop words, so the relaxed (OR) query drops them here; otherwise
# "how does X work" would rank every chunk containing "how"/"does" above real matches.
STOPWORDS = frozenset(
    """
a an the of to in on at by for with from as is are was were be been being do does did doing
how what why when where which who whom whose that this these those it its into than then
and or not no vs versus between about over under can could should would will may might
i you we they he she me my your our their there here so if but also very more most
explain describe difference differences compare define use used using work works
""".split()
)

_TERM_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.\-:]*[A-Za-z0-9]|[A-Za-z0-9]")


def build_filter_clause(
    intent: RetrievalIntent, namespaces: Sequence[str], alias: str = "c"
) -> tuple[str, dict[str, object]]:
    """WHERE fragment (without the WHERE keyword) + bind params for the intent's filters.

    The namespace predicate is ALWAYS present: `namespaces` is the access-controlled
    set from rag.retrieval.access, never the raw intent value.
    """
    if not namespaces:
        raise ValueError("refusing to build a filter with no namespaces")
    a = alias
    open_ns = [n for n in namespaces if n != CANDIDATE_NAMESPACE]
    params: dict[str, object] = {"ns": open_ns}
    if len(open_ns) == len(namespaces):
        clauses = [f"{a}.namespace = ANY(%(ns)s)"]
    else:
        # Candidate chunks are only visible together with an exact owner match. This is enforced
        # here as well as in access.scope_namespaces: a caller that skips the latter still cannot
        # build a predicate that reaches another candidate's (or any unowned) rows.
        if not intent.candidate_id:
            raise ValueError("refusing to build a candidate-namespace filter without candidate_id")
        clauses = [
            f"({a}.namespace = ANY(%(ns)s) OR "
            f"({a}.namespace = '{CANDIDATE_NAMESPACE}' AND {a}.candidate_id = %(cand)s))"
        ]
        params["cand"] = intent.candidate_id
    f = intent.filters
    if f.domain:
        clauses.append(f"{a}.domain = ANY(%(f_domain)s)")
        params["f_domain"] = list(f.domain)
    if f.source_name:
        clauses.append(f"{a}.source_name = ANY(%(f_source)s)")
        params["f_source"] = list(f.source_name)
    if f.related_technologies:
        clauses.append(f"rag_lower_arr({a}.related_technologies) && %(f_tech)s::text[]")
        params["f_tech"] = list(f.related_technologies)
    for col, val, key in (
        ("difficulty", intent.difficulty, "f_diff"),
        ("competency", intent.target_competency, "f_comp"),
    ):
        if not val:
            continue
        eq = f"lower({a}.{col}) = lower(%({key})s)"
        # lenient (default): rows with no value for this attribute (docs, papers) still qualify
        clauses.append(f"({eq})" if intent.strict_attrs else f"({a}.{col} IS NULL OR {eq})")
        params[key] = val
    return " AND ".join(clauses), params


def dense_sql(where: str) -> str:
    return (
        "SELECT c.chunk_id, 1 - (c.embedding <=> %(qvec)s) AS score "
        f"FROM chunks c WHERE {where} "
        "ORDER BY c.embedding <=> %(qvec)s LIMIT %(top_n)s"
    )


# ---------------------------------------------------------------------------
# lexical
# ---------------------------------------------------------------------------


def query_terms(query: str) -> list[str]:
    """Word-ish tokens of the query (identifiers keep their `_ . - :` joiners)."""
    return [m.group(0) for m in _TERM_RE.finditer(query)]


# ALLCAPS words that are ordinary English / SQL keywords / brands, not code identifiers.
ALLCAPS_COMMON = frozenset(
    """
LIMIT ORDER TABLE INDEX HTTPS NVIDIA SELECT WHERE GROUP UNION INNER OUTER CREATE DELETE INSERT
UPDATE VALUES
CHECK FIRST WHILE BEGIN COMMIT ALTER DROP COUNT TRUNCATE CASCADE RETURN RETURNS FALSE TRUE NULL
EXPLAIN ANALYZE VACUUM GRANT REVOKE UNIQUE DEFAULT FOREIGN PRIMARY HAVING OFFSET CROSS WINDOW
ABOUT AFTER ALL ALWAYS BEFORE BETWEEN BOTH EVERY FINAL FIRST HELLO LOCAL MATCH NEVER OTHER SINCE
STILL
THEIR THERE THESE THOSE UNDER UNTIL USING WHERE WHICH WOULD INTEL GOOGLE APPLE AMAZON TESLA
""".split()
)

_ACRONYM_PLURAL = re.compile(r"[A-Z]{2,}s")  # GPUs, APIs, IDs, URLs


def is_identifier(term: str) -> bool:
    """Code-like shape only (so ordinary capitalised words do not create false lexical hits):
    snake_case, ns::name, dotted.path (needs a letter next to the dot), kebab-case between
    alphanumerics,
    camelCase (lower->Upper inside the word), PascalCase with >=2 capitals plus a lowercase letter
    (KStream, DataFrame, PostgreSQL), letter-first letter+digit mixes (gpt4, sha256), and ALLCAPS
    only if
    it contains '_' or a digit, or has >=5 letters and is not a common English/SQL word (XREADGROUP
    yes;
    LIMIT, ORDER, TABLE, INDEX, HTTPS, NVIDIA no). Plural acronyms (GPUs, APIs, IDs) are plain
    words.
    `foo()` and `@decorator` forms are handled by extract_identifiers (the regex term tokenizer
    drops them)."""
    if len(term) < 3 or term.lower() in ("e.g", "i.e", "etc.", "vs."):
        return False
    if "_" in term or "::" in term:
        return True
    if re.search(r"[A-Za-z]\.[A-Za-z0-9]|[0-9]\.[A-Za-z]", term):
        return True
    if re.search(r"[A-Za-z0-9]-[A-Za-z0-9]", term):
        return True
    if term.isalpha() and term.isupper():  # ALLCAPS word
        return len(term) >= 5 and term not in ALLCAPS_COMMON
    if any(c.isupper() for c in term) and any(c.isdigit() for c in term) and term.upper() == term:
        return True  # ALLCAPS with a digit: GPT4, H100
    if _ACRONYM_PLURAL.fullmatch(term):
        return False
    if re.search(r"[a-z][A-Z]", term):  # camelCase
        return True
    if sum(c.isupper() for c in term) >= 2 and any(c.islower() for c in term):  # PascalCase humps
        return True
    if (
        re.fullmatch(r"[A-Za-z][A-Za-z]*\d[A-Za-z\d]*", term) and len(term) >= 3
    ):  # gpt4, sha256, utf8
        return True
    return False


_CALL_RE = re.compile(r"(?<![\w.])([A-Za-z_][\w.]*)\(\)")
_DECORATOR_RE = re.compile(r"@([A-Za-z_][\w.]*)")


def extract_identifiers(query: str, limit: int = 5) -> list[str]:
    out: list[str] = []
    seen = set()

    def add(t: str) -> None:
        if len(t) >= 3 and t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)

    for m in _CALL_RE.finditer(query):  # Depends() -> Depends
        add(m.group(1))
    for m in _DECORATOR_RE.finditer(query):  # @app.get -> app.get
        add(m.group(1))
    for t in query_terms(query):
        if is_identifier(t):
            add(t)
    return out[:limit]


def content_terms(query: str, max_terms: int = 12) -> list[str]:
    out: list[str] = []
    seen = set()
    for t in query_terms(query):
        low = t.lower()
        if low in STOPWORDS or low in seen or (len(low) < 2):
            continue
        seen.add(low)
        out.append(t)
    return out[:max_terms]


def like_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


_WEBSEARCH_OPS = re.compile(r'"|(^|\s)-\w|\bOR\b')


def strict_query_text(query: str) -> str:
    """Text for the AND-ed websearch_to_tsquery. Honour explicit web-search syntax
    (quotes, -term, OR); otherwise AND only the content terms."""
    if _WEBSEARCH_OPS.search(query):
        return query
    return " ".join(content_terms(query))


def lexical_sql(
    where: str,
    n_terms: int,
    n_idents: int,
    config: str = "simple",
    strict_bonus: float = 10.0,
    ident_bonus: float = 0.0,
    components: bool = False,
) -> str:
    """Lexical candidate query.

    match   : any content term (OR-ed websearch_to_tsquery pieces) OR any identifier substring
    score   : ts_rank_cd(relaxed)                         -- proximity/coverage ranking
              + strict_bonus if every content term matches (strict AND query)
              + ident_bonus per identifier found verbatim in the text (max 3), via ILIKE (pg_trgm)
    The weights only decide the order of the candidate list handed to RRF.
    components=True additionally returns (rank_cd, strict, idents) so weights can be tuned offline.
    """
    if config not in LEXICAL_COLUMNS:
        raise ValueError(f"unknown lexical config {config!r}")
    col = f"c.{LEXICAL_COLUMNS[config]}"
    if n_terms == 0 and n_idents == 0:
        raise ValueError("lexical query has no usable terms")
    sb, ib = float(strict_bonus), float(ident_bonus)
    relaxed = (
        " || ".join(f"websearch_to_tsquery('{config}', %(t{i})s)" for i in range(n_terms))
        if n_terms
        else "''::tsquery"
    )
    ident_hits = [f"(c.text ILIKE %(id{i})s ESCAPE '\\')" for i in range(n_idents)]
    idents = "LEAST(" + " + ".join(f"{h}::int" for h in ident_hits) + ", 3)" if ident_hits else "0"
    match = [f"{col} @@ q.relaxed"] if n_terms else []
    match += ident_hits
    rank = f"ts_rank_cd({col}, q.relaxed, 1)"
    strict = f"CASE WHEN {col} @@ q.strict THEN 1 ELSE 0 END"
    score = f"{rank} + {sb} * {strict} + {ib} * {idents}"
    cols = f"c.chunk_id, {score} AS score"
    if components:
        cols += f", {rank} AS rank_cd, {strict} AS strict, {idents} AS idents"
    return (
        f"WITH q AS (SELECT {relaxed} AS relaxed, websearch_to_tsquery('{config}', %(strict)s) AS "
        "strict) "
        f"SELECT {cols} "
        f"FROM chunks c, q WHERE {where} AND ({' OR '.join(match)}) "
        f"ORDER BY score DESC, c.chunk_id LIMIT %(top_n)s"
    )


def lexical_params(query: str) -> tuple[int, int, dict[str, object]]:
    terms = content_terms(query)
    idents = extract_identifiers(query)
    params: dict[str, object] = {"strict": strict_query_text(query)}
    for i, t in enumerate(terms):
        params[f"t{i}"] = t
    for i, t in enumerate(idents):
        params[f"id{i}"] = f"%{like_escape(t)}%"
    return len(terms), len(idents), params


FETCH_SQL = (
    "SELECT chunk_id, text, title, heading_path, source_url, doc_version, namespace, source_name, "
    "domain, difficulty, trust_level, license, candidate_id FROM chunks "
    "WHERE chunk_id = ANY(%(ids)s) AND (namespace = ANY(%(ns)s) "
    "OR (namespace = 'candidate' AND candidate_id = %(cand)s))"  # %(cand)s NULL => never matches
)
