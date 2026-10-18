"""Generic polite HTML crawler -> markdown loader.

Reusable for any docs site (Postgres now; Android docs etc. later) purely
through `options.crawl` in sources.yaml:

    options:
      crawl:
        prefix: "https://www.postgresql.org/docs/current/"   # scope (required)
        start_urls: [".../index.html"]                         # fetched for link discovery
        delay_seconds: 1.5                                     # clamped to >= 1.0
        max_pages: 260
        allow: ["ddl*.html", ...]       # globs on the path *relative to prefix*
        deny:  ["release*.html"]        # deny always wins
        main_selectors: ["#docContent"] # main content container
        strip_selectors: [".navheader", ".navfooter"]
        version_regex: 'PostgreSQL ([0-9.]+) Documentation'
        version_prefix: "PostgreSQL "

Politeness: robots.txt is fetched and honoured (4xx = no rules, 5xx/network
error = do not crawl), descriptive User-Agent, >= 1 s between *network*
requests, hard max_pages cap, raw HTML cached under data/raw/<source>/pages/
so re-runs hit the cache instead of the server.
"""

from __future__ import annotations

import json
import re
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path
from urllib import robotparser
from urllib.parse import urldefrag, urljoin, urlparse

import requests

from ..globs import is_selected
from ..htmlmd import html_to_markdown
from ..registry import Source
from .base import FetchResult, RawDoc

USER_AGENT = (
    "IntelliHireRAGBot/0.1 (NUS-ISS coursework data pipeline; polite crawler, "
    "honours robots.txt; contact: duyingzeng@gmail.com)"
)
MIN_DELAY_SECONDS = 1.0

_HREF_RE = re.compile(r"""<a\b[^>]*?\bhref\s*=\s*["']([^"'#][^"']*|#[^"']*)["']""", re.I)
_TITLE_RE = re.compile(r"^#{1,6}\s+(.*)$", re.M)


class RobotsUnavailable(Exception):
    pass


def load_robots(origin: str, get: Callable[..., requests.Response]) -> robotparser.RobotFileParser:
    rp = robotparser.RobotFileParser()
    url = origin.rstrip("/") + "/robots.txt"
    resp = get(url)
    if resp.status_code == 200:
        rp.parse(resp.text.splitlines())
    elif 400 <= resp.status_code < 500:
        rp.parse([])  # no robots.txt -> everything allowed
    else:
        raise RobotsUnavailable(f"robots.txt returned HTTP {resp.status_code}; not crawling")
    return rp


def normalize_url(url: str) -> str:
    url, _ = urldefrag(url)
    p = urlparse(url)
    return p._replace(query="", fragment="").geturl()


def extract_links(html: str, base_url: str) -> list[str]:
    out = []
    for m in _HREF_RE.finditer(html):
        href = m.group(1).strip()
        if href.startswith(("mailto:", "javascript:", "tel:")):
            continue
        out.append(normalize_url(urljoin(base_url, href)))
    return out


def cache_name(rel: str) -> str:
    rel = rel or "index.html"
    return re.sub(r"[^A-Za-z0-9._-]", "_", rel.replace("/", "__"))


class Crawler:
    def __init__(
        self,
        cfg: dict,
        cache_dir: Path,
        http_get: Callable[..., requests.Response] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.cfg = cfg
        self.prefix: str = cfg["prefix"]
        self.start_urls: list[str] = [
            normalize_url(u) for u in cfg.get("start_urls") or [self.prefix]
        ]
        self.delay = max(float(cfg.get("delay_seconds", 1.5)), MIN_DELAY_SECONDS)
        self.max_pages = int(cfg.get("max_pages", 100))
        self.allow: list[str] = cfg.get("allow") or []
        self.deny: list[str] = cfg.get("deny") or []
        self.cache_dir = cache_dir
        self.sleep, self.clock = sleep, clock
        self._last_net = None  # type: Optional[float]
        self.session = requests.Session()
        self.session.headers["User-Agent"] = cfg.get("user_agent") or USER_AGENT
        self._get = http_get or self._session_get
        self.stats = {
            "network": 0,
            "cache": 0,
            "skipped_scope": 0,
            "robots_blocked": 0,
            "http_errors": 0,
            "redirect_out_of_scope": 0,
            "cap_hit": False,
        }
        self.errors: list[str] = []

    # -- http --------------------------------------------------------------
    def _session_get(self, url: str, **kw) -> requests.Response:
        return self.session.get(url, timeout=30, **kw)

    def _polite_get(self, url: str) -> requests.Response:
        if self._last_net is not None:
            wait = self.delay - (self.clock() - self._last_net)
            if wait > 0:
                self.sleep(wait)
        resp = self._get(url)
        self._last_net = self.clock()
        self.stats["network"] += 1
        return resp

    # -- scope -------------------------------------------------------------
    def rel_path(self, url: str) -> str | None:
        return url[len(self.prefix) :] if url.startswith(self.prefix) else None

    def in_scope_to_store(self, rel: str) -> bool:
        return is_selected(rel, self.allow, self.deny)

    def in_scope_to_visit(self, rel: str) -> bool:
        if rel in {self.rel_path(u) for u in self.start_urls}:
            return True
        return self.in_scope_to_store(rel)

    # -- main --------------------------------------------------------------
    def crawl(self) -> list[tuple[str, str, str]]:
        """Returns [(url, rel_path, html)] for pages that are to be stored."""
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        origin = "{u.scheme}://{u.netloc}".format(u=urlparse(self.prefix))
        try:
            rp = load_robots(origin, self._polite_get)
        except RobotsUnavailable as e:
            self.errors.append(str(e))
            return []
        except requests.RequestException as e:
            self.errors.append(f"robots.txt fetch failed: {e}; not crawling")
            return []
        ua = self.session.headers["User-Agent"]

        queue: deque[str] = deque(self.start_urls)
        seen: set[str] = set(queue)
        pages: list[tuple[str, str, str]] = []
        visited = 0

        while queue:
            if visited >= self.max_pages:
                self.stats["cap_hit"] = True
                break
            url = queue.popleft()
            rel = self.rel_path(url)
            if rel is None or not self.in_scope_to_visit(rel):
                self.stats["skipped_scope"] += 1
                continue
            # robotparser picks the group matching our UA token and only falls back to `*`
            # when none exists, so a Disallow addressed to us is binding even if `*` allows.
            if not rp.can_fetch(ua, url):
                self.stats["robots_blocked"] += 1
                continue

            cache_file = self.cache_dir / cache_name(rel)
            html: str | None = None
            if cache_file.exists():
                html = cache_file.read_text(encoding="utf-8", errors="replace")
                self.stats["cache"] += 1
            else:
                try:
                    try:
                        resp = self._polite_get(url)
                    except requests.RequestException:
                        # one polite retry for transient connection resets
                        self.sleep(5)
                        resp = self._polite_get(url)
                    if resp.status_code in (429, 503):
                        self.sleep(10)
                        resp = self._polite_get(url)
                        if resp.status_code in (429, 503):
                            self.errors.append(
                                f"HTTP {resp.status_code} for {url}; stopping crawl politely"
                            )
                            break
                    if resp.status_code != 200:
                        self.stats["http_errors"] += 1
                        self.errors.append(f"HTTP {resp.status_code}: {url}")
                        continue
                    ctype = resp.headers.get("Content-Type", "") if hasattr(resp, "headers") else ""
                    if ctype and "html" not in ctype.lower():
                        continue
                    final = normalize_url(getattr(resp, "url", None) or url)
                    if final != url:
                        # redirected: the *final* URL must satisfy the same scope + robots rules
                        frel = self.rel_path(final)
                        if frel is None or not self.in_scope_to_visit(frel):
                            self.stats["redirect_out_of_scope"] += 1
                            self.errors.append(f"redirect out of scope ignored: {url} -> {final}")
                            continue
                        if not rp.can_fetch(ua, final):
                            self.stats["robots_blocked"] += 1
                            continue
                    html = resp.text
                    cache_file.write_text(html, encoding="utf-8")
                except requests.RequestException as e:
                    self.stats["http_errors"] += 1
                    self.errors.append(f"request failed for {url}: {e}")
                    continue
            visited += 1

            for link in extract_links(html, url):
                if link not in seen and link.startswith(self.prefix):
                    seen.add(link)
                    queue.append(link)

            if self.in_scope_to_store(rel):
                pages.append((url, rel, html))
        return pages


def _first_heading(md: str) -> str | None:
    m = _TITLE_RE.search(md)
    return m.group(1).strip() if m else None


def apply_permalink(url: str, cfg: dict, version: str) -> str:
    """Rewrite a floating URL (e.g. .../docs/current/x.html) into an immutable one.

    cfg["permalink"] = {"match": "/docs/current/", "replace": "/docs/{major}/"}; {major} is the
    integer
    part of the first number in `version` ("PostgreSQL 18.6" -> 18). If the version cannot be
    determined the URL is left alone (the e2e test then flags it) rather than guessed.

    cfg["permalink"] = {"append": "#snapshot={version}"} (with cfg["static_version"]) is for sites
    that have no versioned URLs (developer.android.com, learnopengl.com): the crawl date is kept
    as doc_version and in the URL fragment, so a chunk can be traced to the snapshot it came from.
    """
    pl = cfg.get("permalink")
    if not pl:
        return url
    if "append" in pl:  # unversioned sites: record the crawl snapshot in a URL fragment
        return url + pl["append"].format(version=version)
    m = re.search(r"(\d+)", version or "")
    if not m or pl["match"] not in url:
        return url
    return url.replace(pl["match"], pl["replace"].format(major=m.group(1)), 1)


def pages_to_docs(
    pages: list[tuple[str, str, str]], cfg: dict, default_version: str = "unknown"
) -> tuple[list[RawDoc], list[str]]:
    version_regex = cfg.get("version_regex")
    version_prefix = cfg.get("version_prefix", "")
    version = default_version
    if cfg.get("static_version"):  # sites without a version string: pin to the crawl snapshot
        version = f"{version_prefix}{cfg['static_version']}"
    if version_regex:
        for _, _, html in pages:
            m = re.search(version_regex, html)
            if m:
                version = version_prefix + m.group(1)
                break
    docs: list[RawDoc] = []
    warnings: list[str] = []
    for url, rel, html in pages:
        md, found = html_to_markdown(
            html,
            main_selectors=cfg.get("main_selectors"),
            strip_selectors=cfg.get("strip_selectors"),
            keep_links=bool(cfg.get("keep_links", False)),
        )
        if not found:
            warnings.append(
                f"main content selector {cfg.get('main_selectors')} not found in {rel}; page "
                "skipped (would leak boilerplate)"
            )
            continue
        if not md.strip():
            continue
        docs.append(
            RawDoc(
                path=rel,
                text=md,
                doc_version=version,
                source_url=apply_permalink(url, cfg, version),
                title=_first_heading(md),
            )
        )
    return docs, warnings


def fetch(source: Source, raw_dir: Path) -> FetchResult:
    cfg = (source.options or {}).get("crawl")
    if not cfg or not cfg.get("prefix"):
        reason = f"crawler source '{source.name}' has no options.crawl.prefix configured — skipping"
        print(f"[fetch] {reason}")
        return FetchResult(source_name=source.name, skipped=True, skip_reason=reason)

    crawler = Crawler(cfg, raw_dir / source.name / "pages")
    pages = crawler.crawl()
    docs, warnings = pages_to_docs(pages, cfg)

    s = crawler.stats
    notes = [
        f"crawl: {len(pages)} page(s) stored ({s['network']} network request(s) incl. robots.txt, "
        f"{s['cache']} from cache), "
        f"{s['skipped_scope']} out-of-scope link(s) skipped, {s['robots_blocked']} blocked by "
        "robots.txt, "
        f"{s['http_errors']} HTTP error(s), {s['redirect_out_of_scope']} out-of-scope redirect(s), "
        f"delay>={crawler.delay}s, cap={crawler.max_pages}"
        + (" — CAP HIT (raise max_pages or tighten allow list)" if s["cap_hit"] else "")
    ]
    try:
        (raw_dir / source.name / "_crawl_manifest.json").write_text(
            json.dumps({"stats": s, "pages": [p[1] for p in pages]}, indent=2), encoding="utf-8"
        )
    except OSError:
        pass
    errors = crawler.errors + warnings
    skipped = not docs and bool(crawler.errors)
    print(f"[fetch] {source.name}: {len(docs)} pages -> markdown ({notes[0]})")
    return FetchResult(
        source_name=source.name,
        docs=docs,
        errors=errors,
        notes=notes,
        skipped=skipped,
        skip_reason=(crawler.errors[0] if skipped else None),
    )
