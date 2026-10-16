"""Load & validate data/sources.yaml."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

VALID_NAMESPACES = {"candidate", "job", "technical", "rubric", "question", "test"}
VALID_LOADERS = {"git", "hf", "arxiv", "crawler", "manual", "xlsx", "wikipedia"}
VALID_PRIORITIES = {"P0", "P1", "P2"}
VALID_AUTO_LEVELS = {"full", "semi", "manual"}
VALID_CLEAN_PROFILES = {
    "markdown",
    "rst",
    "d2l",
    "mintlify",
    "hugo",
    "html",
    "mdn",
    "asciidoc",
    "msdocs",
    "jekyll",
    "quarto",
    "notebook",
    "writerside",
    "docc",
    "eleventy",
    "doxygen",
    "mkdocs",
    "flecs",
    "zola",
    "xdoc",
    "docusaurus",
    "myst",
    "mmlab",
}
VALID_TRUST_LEVELS = {"official", "curated", "community", "reference_only"}


@dataclass
class Source:
    name: str
    namespace: str
    priority: str
    url: str
    loader: str
    license: str
    auto_level: str
    trust_level: str
    refresh_cycle: str
    domain: str
    related_technologies: list[str] = field(default_factory=list)
    reference_only: bool = False
    path: str | None = None
    paths: list[str] | None = None
    verified: bool = True
    notes: str | None = None
    # --- declarative scoping (git / crawler) ---
    # include/exclude: glob lists matched against repo-relative paths (git) or
    # URL paths (crawler). `**` crosses directories, `*` does not.
    include: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)
    max_file_kb: int | None = None  # skip generated/oversized files
    # cleaning profile applied before the generic cleaner:
    # markdown (default) | rst | d2l | mintlify | hugo | html
    clean_profile: str = "markdown"
    # free-form per-loader options (hf: files/dedupe_against; crawler: crawl
    # settings and selectors; xlsx: parser name; git: py_gallery, ...)
    options: dict[str, Any] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> list[str]:
        errors = []
        if self.namespace not in VALID_NAMESPACES:
            errors.append(f"{self.name}: invalid namespace '{self.namespace}'")
        if self.priority not in VALID_PRIORITIES:
            errors.append(f"{self.name}: invalid priority '{self.priority}'")
        if self.loader not in VALID_LOADERS:
            errors.append(f"{self.name}: invalid loader '{self.loader}'")
        if self.auto_level not in VALID_AUTO_LEVELS:
            errors.append(f"{self.name}: invalid auto_level '{self.auto_level}'")
        if self.trust_level not in VALID_TRUST_LEVELS:
            errors.append(f"{self.name}: invalid trust_level '{self.trust_level}'")
        if self.clean_profile not in VALID_CLEAN_PROFILES:
            errors.append(f"{self.name}: invalid clean_profile '{self.clean_profile}'")
        if not self.license:
            errors.append(f"{self.name}: missing license")
        return errors


def load_registry(path: Path) -> list[Source]:
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    sources_raw = raw.get("sources", [])
    sources: list[Source] = []
    known_fields = {
        "name",
        "namespace",
        "priority",
        "url",
        "loader",
        "license",
        "auto_level",
        "trust_level",
        "refresh_cycle",
        "domain",
        "related_technologies",
        "reference_only",
        "path",
        "paths",
        "verified",
        "notes",
        "include",
        "exclude",
        "max_file_kb",
        "clean_profile",
        "options",
    }
    for entry in sources_raw:
        kwargs = {k: v for k, v in entry.items() if k in known_fields}
        extra = {k: v for k, v in entry.items() if k not in known_fields}
        src = Source(**kwargs, extra=extra)
        sources.append(src)
    return sources


def validate_registry(sources: list[Source]) -> list[str]:
    errors: list[str] = []
    seen_names = set()
    for s in sources:
        errors.extend(s.validate())
        if s.name in seen_names:
            errors.append(f"duplicate source name: {s.name}")
        seen_names.add(s.name)
    return errors


def get_source(sources: list[Source], name: str) -> Source | None:
    for s in sources:
        if s.name == name:
            return s
    return None


def filter_sources(
    sources: list[Source],
    name: str | None = None,
    priority: str | None = None,
) -> list[Source]:
    out = sources
    if name:
        out = [s for s in out if s.name == name]
    if priority:
        out = [s for s in out if s.priority == priority]
    return out
