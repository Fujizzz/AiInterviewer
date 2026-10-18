import pytest

# Optional rag-extra dependencies: skip the whole module when they are not installed.
pytest.importorskip("requests")
from rag.loaders.crawler_loader import (
    MIN_DELAY_SECONDS,
    USER_AGENT,
    Crawler,
    extract_links,
    normalize_url,
    pages_to_docs,
)

PREFIX = "https://docs.example.org/docs/current/"


class FakeResp:
    def __init__(self, text="", status=200, ctype="text/html"):
        self.text, self.status_code, self.headers = text, status, {"Content-Type": ctype}


def page(title, links=(), body="<p>hello</p>"):
    a = "".join(f'<a href="{link}">l</a>' for link in links)
    return (
        f'<html><body><div id="main"><h1>{title}</h1>{body}{a}</div><span>PostgreSQL 18.6 '
        "Documentation</span></body></html>"
    )


class Net:
    def __init__(self, pages, robots="User-agent: *\nDisallow: /docs/devel/\n"):
        self.pages, self.robots, self.calls = pages, robots, []

    def __call__(self, url, **kw):
        self.calls.append(url)
        if url.endswith("/robots.txt"):
            return FakeResp(self.robots) if self.robots is not None else FakeResp("", 404)
        return FakeResp(self.pages[url]) if url in self.pages else FakeResp("", 404)


def make(tmp_path, pages, **cfg):
    base = {
        "prefix": PREFIX,
        "start_urls": [PREFIX + "index.html"],
        "delay_seconds": 1.5,
        "max_pages": 50,
        "allow": ["ddl*.html", "mvcc.html"],
        "deny": ["*print*"],
    }
    base.update(cfg)
    t = {"now": 0.0}
    sleeps = []

    def sleep(s):
        sleeps.append(s)
        t["now"] += s

    net = Net(pages)
    c = Crawler(base, tmp_path, http_get=net, sleep=sleep, clock=lambda: t["now"])
    return c, net, sleeps


def test_scope_allow_deny_and_prefix(tmp_path):
    pages = {
        PREFIX + "index.html": page(
            "Index",
            [
                "ddl.html",
                "mvcc.html",
                "release-18.html",
                "ddl-print.html",
                "https://other.org/ddl.html",
            ],
        ),
        PREFIX + "ddl.html": page("DDL"),
        PREFIX + "mvcc.html": page("MVCC"),
        PREFIX + "release-18.html": page("Release"),
        PREFIX + "ddl-print.html": page("Print"),
    }
    c, net, _ = make(tmp_path, pages)
    got = c.crawl()
    assert sorted(r for _, r, _ in got) == [
        "ddl.html",
        "mvcc.html",
    ]  # index fetched for discovery only
    assert not any("other.org" in u or "release" in u or "print" in u for u in net.calls)


def test_robots_disallow_is_honoured(tmp_path):
    pages = {PREFIX + "index.html": page("I", ["ddl.html"]), PREFIX + "ddl.html": page("DDL")}
    c, net, _ = make(tmp_path, pages)
    net.robots = "User-agent: *\nDisallow: /docs/current/ddl.html\n"
    got = c.crawl()
    assert got == [] and c.stats["robots_blocked"] == 1
    assert PREFIX + "ddl.html" not in net.calls


def test_robots_server_error_means_no_crawl(tmp_path):
    c, net, _ = make(tmp_path, {})
    net.__class__ = type("N2", (Net,), {"__call__": lambda self, url, **kw: FakeResp("", 503)})
    assert c.crawl() == [] and "robots.txt" in c.errors[0]


def test_delay_enforced_between_network_requests(tmp_path):
    pages = {
        PREFIX + "index.html": page("I", ["ddl.html", "mvcc.html"]),
        PREFIX + "ddl.html": page("D"),
        PREFIX + "mvcc.html": page("M"),
    }
    c, _, sleeps = make(tmp_path, pages, delay_seconds=0.1)  # below the floor on purpose
    c.crawl()
    assert c.delay >= MIN_DELAY_SECONDS
    assert len(sleeps) >= 3 and all(s >= MIN_DELAY_SECONDS - 1e-9 for s in sleeps)


def test_descriptive_user_agent():
    assert "IntelliHire" in USER_AGENT and "contact" in USER_AGENT


def test_max_pages_cap(tmp_path):
    pages = {PREFIX + "index.html": page("I", [f"ddl{i}.html" for i in range(10)])}
    pages.update({PREFIX + f"ddl{i}.html": page(str(i)) for i in range(10)})
    c, _, _ = make(tmp_path, pages, max_pages=4)
    got = c.crawl()
    assert len(got) == 3 and c.stats["cap_hit"]  # index + 3 pages visited


def test_cache_prevents_second_network_fetch(tmp_path):
    pages = {PREFIX + "index.html": page("I", ["ddl.html"]), PREFIX + "ddl.html": page("D")}
    c1, net1, _ = make(tmp_path, pages)
    c1.crawl()
    c2, net2, _ = make(tmp_path, pages)
    c2.crawl()
    assert [u for u in net2.calls if u.endswith(".html")] == []  # only robots.txt hit the network
    assert c2.stats["cache"] == 2


def test_pages_to_docs_version_title_and_boilerplate_guard():
    pages = [
        (PREFIX + "ddl.html", "ddl.html", page("Data Definition", body="<p>tables</p>")),
        (PREFIX + "bad.html", "bad.html", "<html><body><p>no main div</p></body></html>"),
    ]
    cfg = {
        "main_selectors": ["#main"],
        "version_regex": r"PostgreSQL ([0-9.]+) Documentation",
        "version_prefix": "PostgreSQL ",
    }
    docs, warnings = pages_to_docs(pages, cfg)
    assert len(docs) == 1 and docs[0].doc_version == "PostgreSQL 18.6"
    assert docs[0].source_url == PREFIX + "ddl.html" and docs[0].title == "Data Definition"
    assert warnings and "bad.html" in warnings[0]


def test_link_extraction_and_normalisation():
    html = '<a href="a.html#x">1</a><a href="mailto:a@b">2</a><a href="../up.html?q=1">3</a>'
    assert extract_links(html, PREFIX + "p/q.html") == [
        PREFIX + "p/a.html",
        "https://docs.example.org/docs/current/up.html",
    ]
    assert normalize_url("https://x.org/a.html?x=1#y") == "https://x.org/a.html"


def test_transient_connection_error_is_retried_once(tmp_path):
    import requests

    pages = {PREFIX + "index.html": page("I", ["ddl.html"]), PREFIX + "ddl.html": page("D")}
    c, net, sleeps = make(tmp_path, pages)
    state = {"failed": False}
    real = net.__call__

    def flaky(url, **kw):
        if url.endswith("ddl.html") and not state["failed"]:
            state["failed"] = True
            raise requests.ConnectionError("reset")
        return real(url, **kw)

    c._get = flaky
    got = c.crawl()
    assert [r for _, r, _ in got] == ["ddl.html"] and 5 in sleeps
