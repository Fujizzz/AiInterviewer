"""Git-backed docs loader.

Uses a shallow, blobless, sparse clone so we only ever download the subset
of files a source actually needs:

    git clone --depth 1 --filter=blob:none --sparse <url>
    git sparse-checkout init --no-cone
    git sparse-checkout set <patterns>

Re-running is idempotent: if the repo dir already exists we `git fetch
--depth 1` + reset to the new HEAD instead of re-cloning.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from ..globs import _expand_braces, is_selected
from ..registry import Source
from ..schema import count_tokens
from .base import FetchResult, RawDoc

DEFAULT_EXTS = {".md", ".mdx", ".rst", ".ipynb"}

# pymdownx.snippets include directive, e.g.:
#   --8<-- "vllm/envs.py:env-vars-definition"
#   --8<-- "docs/getting_started/installation/gpu.cuda.inc.md"
# Only matched on its own line (optionally indented) to avoid false
# positives from prose that merely *mentions* the syntax.
_SNIPPET_INCLUDE_RE = re.compile(r'^([ \t]*)--8<--\s+"([^":]+)(?::([^"]+))?"\s*$', re.M)

_FENCED_NOTE_RE = re.compile(
    r"^([ \t]*)(```|~~~)[^\n]*\n[ \t]*(\((?:full content omitted|code example |snippet |generated "
    r")[^\n]*\))[ \t]*\n[ \t]*\2[ \t]*$",
    re.M,
)

# Inlining an arbitrarily large source file as a single fenced code block
# would itself become one giant un-splittable chunk (the same failure mode
# as "never split inside a table/fence" on a huge table) — above this size
# we strip the directive instead of inlining, same as "file not found".
SNIPPET_INLINE_MAX_TOKENS = 2000


# ---------------------------------------------------------------------------
# FastAPI-style code includes (resolved against `options.code_include_base`):
#   {* ../../docs_src/x/tutorial001.py hl[5:9] ln[1:12] title["app/main.py"] *}   (standalone line
#   -> new fence)
#   {!> ../../docs_src/x/tutorial001.py!}                                         (legacy; usually
#   inside a fence)
# `hl[...]` only highlights lines (dropped); `ln[a:b,c]` selects lines (1-based, inclusive).
# ---------------------------------------------------------------------------

_CODE_INCLUDE_RE = re.compile(r"^([ \t]*)\{\*[ \t]+(\S+)([^\n]*?)[ \t]*\*\}[ \t]*$", re.M)
_LEGACY_INCLUDE_RE = re.compile(r"^([ \t]*)\{![>]?[ \t]*([^\s!]+)[ \t]*!\}[ \t]*$", re.M)
_LANG_BY_EXT = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".json": "json",
    ".html": "html",
    ".css": "css",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".sh": "bash",
    ".txt": "",
    ".jinja": "html",
    ".md": "markdown",
    ".sql": "sql",
    ".java": "java",
    ".go": "go",
}
_GAP_MARK = {
    "python": "# ...",
    "bash": "# ...",
    "yaml": "# ...",
    "toml": "# ...",
    "javascript": "// ...",
    "typescript": "// ...",
    "css": "/* ... */",
}


def _code_include_target(base: str, rel: str) -> str | None:
    """Repo-relative POSIX path of an include target, or None if it escapes the repo."""
    import posixpath

    norm = posixpath.normpath(posixpath.join(base, rel))
    return None if norm.startswith("..") or norm.startswith("/") else norm


def _select_lines(content: str, spec: str) -> tuple[list[str], bool]:
    """Applies `ln[...]` ranges. Returns (lines, has_gaps)."""
    lines = content.rstrip("\n").split("\n")
    picked: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        a, _, b = part.partition(":")
        try:
            lo = int(a)
            hi = int(b) if b else lo
        except ValueError:
            continue
        picked.extend(range(max(lo, 1), min(hi, len(lines)) + 1))
    if not picked:
        return lines, False
    picked = sorted(set(picked))
    out: list[str] = []
    prev = None
    gaps = False
    for n in picked:
        if prev is not None and n != prev + 1:
            out.append("\0GAP")
            gaps = True
        out.append(lines[n - 1])
        prev = n
    if picked[0] != 1:
        out.insert(0, "\0GAP")
        gaps = True
    if picked[-1] != len(lines):
        out.append("\0GAP")
        gaps = True
    return out, gaps


def substitute_code_includes_in_text(
    text: str,
    base: str,
    file_cache: dict[str, str | None],
    permalink_base: str | None = None,
    max_tokens: int = SNIPPET_INLINE_MAX_TOKENS,
) -> tuple[str, dict[str, int]]:
    """Pure substitution (no filesystem access). `file_cache` maps repo-relative
    path -> content (None = not available). Returns (new_text, counts) with counts
    keys inlined / too_large / unresolved."""
    counts = {"inlined": 0, "too_large": 0, "unresolved": 0}

    def resolve(indent: str, rel: str, opts: str, legacy: bool) -> str:
        target = _code_include_target(base, rel)
        link = f"{permalink_base}/{target}" if (permalink_base and target) else rel

        def note(why: str) -> str:
            return f"{indent}({why}; see upstream: {link})"

        content = file_cache.get(target) if target else None
        if content is None:
            counts["unresolved"] += 1
            return note("code example not available in this checkout")
        ext = "." + target.rsplit(".", 1)[-1].lower() if "." in target.rsplit("/", 1)[-1] else ""
        lang = _LANG_BY_EXT.get(ext, "")
        ln = re.search(r"\bln\[([^\]]*)\]", opts)
        if ln:
            lines, _ = _select_lines(content, ln.group(1))
            mark = _GAP_MARK.get(lang)
            lines = [
                mark if (x == "\0GAP" and mark) else x
                for x in lines
                if not (x == "\0GAP" and not mark)
            ]
        else:
            lines = content.rstrip("\n").split("\n")
        body = "\n".join(lines)
        if count_tokens(body) > max_tokens:
            counts["too_large"] += 1
            return note("full content omitted for size")
        counts["inlined"] += 1
        if legacy:
            return "\n".join((indent + x) if x.strip() else x for x in lines)
        fence = "```"
        while fence in body:
            fence += "`"
        title = re.search(r'\btitle\["([^"]*)"\]', opts)
        head = f"{indent}**{title.group(1)}**\n\n" if title else ""
        return head + "\n".join(
            [f"{indent}{fence}{lang}"]
            + [(indent + x) if x.strip() else x for x in lines]
            + [f"{indent}{fence}"]
        )

    text = _CODE_INCLUDE_RE.sub(lambda m: resolve(m.group(1), m.group(2), m.group(3), False), text)
    text = _LEGACY_INCLUDE_RE.sub(lambda m: resolve(m.group(1), m.group(2), "", True), text)
    text = _FENCED_NOTE_RE.sub(r"\1\3", text)
    return text, counts


def resolve_code_includes(
    repo_dir: Path, docs: list[RawDoc], base: str, permalink_base: str | None = None
) -> list[str]:
    """Widens the sparse checkout for any include target that is not on disk yet
    (e.g. `fastapi/openapi/docs.py`), reads the targets and inlines them."""
    needed: dict[str, None] = {}
    for d in docs:
        for rx in (_CODE_INCLUDE_RE, _LEGACY_INCLUDE_RE):
            for m in rx.finditer(d.text):
                t = _code_include_target(base, m.group(2))
                if t:
                    needed[t] = None
    if not any(_CODE_INCLUDE_RE.search(d.text) or _LEGACY_INCLUDE_RE.search(d.text) for d in docs):
        return []
    missing = [p for p in needed if not (repo_dir / p).exists()]
    if missing:
        try:
            _run(["git", "sparse-checkout", "add", *missing], cwd=repo_dir)
        except Exception as e:
            print(f"[fetch] WARNING could not widen sparse-checkout for code includes: {e}")
    cache: dict[str, str | None] = {}
    for t in needed:
        f = repo_dir / t
        cache[t] = f.read_text(encoding="utf-8", errors="ignore") if f.is_file() else None
    total = {"inlined": 0, "too_large": 0, "unresolved": 0}
    unresolved: list[str] = []
    for d in docs:
        d.text, c = substitute_code_includes_in_text(
            d.text, base, cache, permalink_base=permalink_base
        )
        for k in total:
            total[k] += c[k]
        if c["unresolved"]:
            unresolved.append(d.path)
    note = (
        f"code-include ({{* *}} / {{!> !}}): {sum(total.values())} occurrence(s) — "
        f"{total['inlined']} inlined as fenced code, "
        f"{total['too_large']} replaced by an upstream-permalink note (target > "
        f"{SNIPPET_INLINE_MAX_TOKENS} tokens), "
        f"{total['unresolved']} unresolved (upstream-permalink note)"
        + (f" in {', '.join(unresolved[:5])}" if unresolved else "")
    )
    print(f"[fetch] {note}")
    return [note]


def _run(cmd: list[str], cwd: Path | None = None) -> str:
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"command failed: {' '.join(cmd)}\n{result.stderr.strip()}")
    return result.stdout.strip()


def _owner_repo_from_url(url: str) -> tuple[str, str]:
    cleaned = url.rstrip("/")
    if cleaned.endswith(".git"):
        cleaned = cleaned[: -len(".git")]
    parts = cleaned.split("/")
    return parts[-2], parts[-1]


def _sparse_patterns(raw_paths: list[str], exclude: list[str] | None = None) -> list[str] | None:
    """Returns None to signal "full checkout, no sparse restriction".

    Directory-shaped exclude globs (`some/dir/**`) are also pushed down into
    the sparse-checkout as gitignore-style negations so we never even
    download the blobs of e.g. 500+ per-model doc pages."""
    patterns = []
    expanded: list[str] = []
    for p in raw_paths:
        expanded.extend(_expand_braces(p) if p else [p])  # git sparse patterns have no {a,b}
    for p in expanded:
        if p in (".", "", None):
            return None
        if any(ch in p for ch in "*?[") or p.endswith(
            (".md", ".mdx", ".rst", ".txt", ".ipynb", ".py")
        ):
            patterns.append(p)
        else:
            patterns.append(p.rstrip("/") + "/")
    for ex_raw in exclude or []:
        for ex in _expand_braces(ex_raw):
            if ex.endswith("/**") and not any(ch in ex[:-3] for ch in "*?["):
                patterns.append("!" + ex[:-3].rstrip("/") + "/")
    return patterns


_EXT_FROM_GLOB_RE = re.compile(r"\*\.(\w+)$")


def _extra_exts(raw_paths: list[str]) -> set:
    exts = set()
    for p in raw_paths:
        m = _EXT_FROM_GLOB_RE.search(p)
        if m:
            exts.add("." + m.group(1))
    return exts


def _extract_ipynb_markdown(raw_text: str) -> str:
    try:
        nb = json.loads(raw_text)
    except Exception:
        return ""
    parts = []
    for cell in nb.get("cells", []):
        if cell.get("cell_type") == "markdown":
            src = cell.get("source", "")
            if isinstance(src, list):
                src = "".join(src)
            if src.strip():
                parts.append(src)
    return "\n\n".join(parts)


def find_needed_snippets(docs: list[RawDoc]) -> dict[str, set[str]]:
    """Scans every doc's text for `--8<-- "path[:section]"` directives.
    Returns {referenced_path: {section_name, ...}} (empty set = whole file)."""
    needed: dict[str, set[str]] = {}
    for doc in docs:
        for m in _SNIPPET_INCLUDE_RE.finditer(doc.text):
            path, section = m.group(2), m.group(3)
            needed.setdefault(path, set())
            if section:
                needed[path].add(section)
    return needed


def extract_snippet_section(file_content: str, section: str) -> str | None:
    """Pulls the text between `--8<-- [start:<section>]` and
    `--8<-- [end:<section>]` marker comments in a source file. Returns None
    if the markers aren't found."""
    pattern = re.compile(
        r"--8<--\s*\[\s*start:\s*" + re.escape(section) + r"\s*\].*?\n(.*?)"
        r"--8<--\s*\[\s*end:\s*" + re.escape(section) + r"\s*\]",
        re.S,
    )
    m = pattern.search(file_content)
    return m.group(1) if m else None


def substitute_snippet_includes_in_text(
    text: str,
    file_cache: dict[str, str | None],
    max_tokens: int = SNIPPET_INLINE_MAX_TOKENS,
    permalink_base: str | None = None,
) -> tuple[str, int, int, int]:
    """Pure substitution step (no filesystem/git access) — kept separate
    from `resolve_snippet_includes` so it's unit-testable without a real
    repo. Returns (new_text, resolved_count, not_found_count, too_large_count)."""
    counts = {"resolved": 0, "not_found": 0, "too_large": 0}

    def _sub(m: re.Match) -> str:
        indent, path, section = m.group(1), m.group(2), m.group(3)
        link = f"{permalink_base}/{path}" if permalink_base else path

        def note(why: str) -> str:
            # never leave an empty code fence behind: say what was omitted and where it lives
            return f"{indent}({why}; see upstream: {link})"

        if "/" not in path and "." not in path:
            # virtual include (mkdocs-gen-files, e.g. `gen:engine-args`): no such file exists in the
            # repo
            counts["not_found"] += 1
            return (
                f"{indent}(generated table/listing omitted: produced at docs build time, "
                "not stored in the repository)"
            )

        content = file_cache.get(path)
        if content is None:
            counts["not_found"] += 1
            return note("snippet source not available in this checkout")
        snippet = extract_snippet_section(content, section) if section else content
        if snippet is None:
            counts["not_found"] += 1
            return note("snippet section not found")
        if count_tokens(snippet) > max_tokens:
            counts["too_large"] += 1
            return note("full content omitted for size")
        lines = snippet.strip("\n").split("\n")
        counts["resolved"] += 1
        return "\n".join((indent + ln if ln.strip() else ln) for ln in lines)

    new_text = _SNIPPET_INCLUDE_RE.sub(_sub, text)
    # a fence whose only content is an omission note is not code: unwrap it
    new_text = _FENCED_NOTE_RE.sub(r"\1\3", new_text)
    return new_text, counts["resolved"], counts["not_found"], counts["too_large"]


_SNIPPET_MAX_PASSES = 5


def resolve_snippet_includes(
    repo_dir: Path, docs: list[RawDoc], permalink_base: str | None = None
) -> list[str]:
    """Widens the sparse-checkout to pull in any file referenced by a
    `--8<-- "path[:section]"` directive that lives inside this same repo,
    then inlines the referenced content (or named section) in place of the
    directive — unless it's too large to be a sane chunk, in which case the
    directive line is stripped instead. Returns human-readable summary notes
    for the run report; does not raise on individual failures.

    Runs in multiple passes (bounded by `_SNIPPET_MAX_PASSES`) because an
    inlined section can itself contain further `--8<--` directives — e.g.
    vllm's docker.md includes a named section of gpu.md, and *that* section's
    own text contains nested includes of gpu.cuda.inc.md/gpu.rocm.inc.md/etc.
    A single non-recursive substitution pass would leave those nested
    directives as literal unresolved text in the final chunk.
    """
    total_occurrences = sum(len(_SNIPPET_INCLUDE_RE.findall(d.text)) for d in docs)
    if not total_occurrences:
        return []

    resolved_total = not_found_total = too_large_total = 0
    passes_used = 0

    for passes_used in range(1, _SNIPPET_MAX_PASSES + 1):  # noqa: B007 (read after the loop)
        needed = find_needed_snippets(docs)
        if not needed:
            break

        missing = [p for p in needed if not (repo_dir / p).exists()]
        if missing:
            try:
                _run(["git", "sparse-checkout", "add", *missing], cwd=repo_dir)
            except Exception as e:
                print(
                    "[fetch] WARNING could not widen sparse-checkout for snippet includes "
                    f"{missing}: {e}"
                )

        file_cache: dict[str, str | None] = {}
        for path in needed:
            fpath = repo_dir / path
            if fpath.is_file():
                try:
                    file_cache[path] = fpath.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    file_cache[path] = None
            else:
                file_cache[path] = None

        pass_resolved = 0
        for doc in docs:
            doc.text, resolved, not_found, too_large = substitute_snippet_includes_in_text(
                doc.text, file_cache, permalink_base=permalink_base
            )
            resolved_total += resolved
            not_found_total += not_found
            too_large_total += too_large
            pass_resolved += resolved

        remaining = sum(len(_SNIPPET_INCLUDE_RE.findall(d.text)) for d in docs)
        if remaining == 0 or pass_resolved == 0:
            # Either fully done, or whatever's left is a not-found/too-large
            # case that was already stripped — nothing new will appear from
            # another pass, so stop instead of burning the remaining budget.
            break

    leftover = sum(len(_SNIPPET_INCLUDE_RE.findall(d.text)) for d in docs)
    if leftover:
        print(
            f"[fetch] WARNING {leftover} snippet-include directive(s) still unresolved after "
            f"{passes_used} pass(es) (hit _SNIPPET_MAX_PASSES={_SNIPPET_MAX_PASSES} — possible "
            "deep/cyclic nesting)"
        )

    note = (
        f"snippet-include (--8<--): {total_occurrences} occurrence(s) found across the fetched "
        "docs — "
        f"{resolved_total} resolved inline ({passes_used} pass(es), nested includes re-scanned), "
        f"{not_found_total} stripped (target file/section not fetchable within this repo), "
        f"{too_large_total} stripped (target exceeds {SNIPPET_INLINE_MAX_TOKENS} tokens — inlining "
        "it would "
        "create one giant un-splittable chunk, same tradeoff as never splitting a table)"
        + (f", {leftover} left unresolved (nesting too deep)" if leftover else "")
    )
    print(f"[fetch] {note}")
    return [note]


def fetch(source: Source, raw_dir: Path) -> FetchResult:
    if not source.url or "github.com" not in source.url:
        reason = f"git loader requires a github.com url, got {source.url!r}"
        print(f"[fetch] {reason}")
        return FetchResult(source_name=source.name, skipped=True, skip_reason=reason)

    owner, repo = _owner_repo_from_url(source.url)
    clone_url = f"https://github.com/{owner}/{repo}.git"
    repo_dir = raw_dir / source.name / "_repo"
    raw_paths = source.paths if source.paths else ([source.path] if source.path else ["."])
    patterns = _sparse_patterns(raw_paths, source.exclude)

    errors: list[str] = []
    notes: list[str] = []
    try:
        if not repo_dir.exists():
            repo_dir.parent.mkdir(parents=True, exist_ok=True)
            print(f"[fetch] cloning {clone_url} -> {repo_dir}")
            _run(
                [
                    "git",
                    "clone",
                    "--depth",
                    "1",
                    "--filter=blob:none",
                    "--sparse",
                    clone_url,
                    str(repo_dir),
                ]
            )
            if patterns is not None:
                _run(["git", "sparse-checkout", "init", "--no-cone"], cwd=repo_dir)
                _run(["git", "sparse-checkout", "set", *patterns], cwd=repo_dir)
        else:
            print(f"[fetch] refreshing existing clone at {repo_dir}")
            branch = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_dir)
            _run(["git", "fetch", "--depth", "1", "origin", branch], cwd=repo_dir)
            _run(["git", "reset", "--hard", f"origin/{branch}"], cwd=repo_dir)
            if patterns is not None:
                _run(["git", "sparse-checkout", "set", *patterns], cwd=repo_dir)
    except Exception as e:
        msg = str(e)
        print(f"[fetch] ERROR fetching {source.name}: {msg}")
        return FetchResult(source_name=source.name, skipped=True, skip_reason=msg, errors=[msg])

    try:
        commit = _run(["git", "rev-parse", "HEAD"], cwd=repo_dir)
    except Exception as e:
        msg = f"could not resolve HEAD: {e}"
        return FetchResult(source_name=source.name, skipped=True, skip_reason=msg, errors=[msg])

    allowed_exts = DEFAULT_EXTS | _extra_exts(raw_paths)
    if source.options.get("exts"):
        allowed_exts = set(source.options["exts"])
    elif source.options.get("py_gallery"):
        allowed_exts = allowed_exts | {".py"}
    docs: list[RawDoc] = []
    n_filtered = n_too_big = 0
    size_skipped: list[tuple[str, int]] = []
    for path in sorted(repo_dir.rglob("*")):
        if path.is_dir() or ".git" in path.parts:
            continue
        if path.suffix not in allowed_exts:
            continue
        rel = path.relative_to(repo_dir).as_posix()
        if not is_selected(rel, source.include, source.exclude):
            n_filtered += 1
            continue
        if source.max_file_kb and path.stat().st_size > source.max_file_kb * 1024:
            n_too_big += 1
            size_skipped.append((rel, path.stat().st_size))
            continue
        try:
            raw_text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception as e:
            errors.append(f"{rel}: read failed ({e})")
            continue

        if path.suffix == ".ipynb":
            raw_text = _extract_ipynb_markdown(raw_text)

        if not raw_text.strip():
            continue

        permalink = f"https://github.com/{owner}/{repo}/blob/{commit}/{rel}"
        docs.append(RawDoc(path=rel, text=raw_text, doc_version=commit, source_url=permalink))

    if n_filtered or n_too_big:
        notes.append(
            f"scope filter: {n_filtered} candidate file(s) skipped by include/exclude globs, "
            f"{n_too_big} skipped as > max_file_kb={source.max_file_kb}"
        )
    for rel, size in size_skipped:
        notes.append(
            f"skipped for size: {rel} ({size / 1024:.0f} KB > max_file_kb={source.max_file_kb})"
        )
    code_base = (source.options or {}).get("code_include_base")
    if code_base:
        try:
            notes.extend(
                resolve_code_includes(
                    repo_dir,
                    docs,
                    code_base,
                    permalink_base=f"https://github.com/{owner}/{repo}/blob/{commit}",
                )
            )
        except Exception as e:
            errors.append(f"code-include resolution failed: {e}")
            print(f"[fetch] WARNING code-include resolution failed for {source.name}: {e}")
    try:
        snippet_notes = resolve_snippet_includes(
            repo_dir, docs, permalink_base=f"https://github.com/{owner}/{repo}/blob/{commit}"
        )
        notes.extend(snippet_notes)
    except Exception as e:
        errors.append(f"snippet-include resolution failed: {e}")
        print(f"[fetch] WARNING snippet-include resolution failed for {source.name}: {e}")

    print(f"[fetch] {source.name}: {len(docs)} files, HEAD={commit[:12]}")
    return FetchResult(source_name=source.name, docs=docs, errors=errors, notes=notes)
