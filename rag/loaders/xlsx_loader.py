"""Excel loader for the SkillsFuture Singapore "Skills Framework for Infocomm
Technology (ICT)" workbook -> one record per job role.

IMPORTANT: the official file could not be downloaded programmatically (the
public page is a JS shell and the Jobs-Skills Portal returns HTTP 403 to
scripts), so this parser is written against the *documented* structure and
tested on synthetic fixtures only. Place the manually-downloaded workbook(s)
in data/raw/skillsfuture-ict/*.xlsx.

Assumed structure (header-driven and tolerant — sheets are recognised by
their column headers, not by sheet name, and headers are matched fuzzily):

  * job-role description sheet — columns like: Sector | Track | Job Role |
    Job Role Description | Critical Work Function | Key Tasks
  * TSC mapping sheet — Sector | Track | Job Role | TSC Category | TSC Title |
    EITHER one "Proficiency Level" column, OR six "Level 1" .. "Level 6" columns
    where the non-empty cell(s) mark the required level (highest one wins)
  * CCS mapping sheet — Sector | Track | Job Role | CCS / Generic Skill Title |
    Proficiency Level

Merged / blank cells in the Sector/Track/Job Role columns are filled down. A sheet that
lacks Sector/Track columns (or leaves them blank) is attached to the *existing* role with
the same title instead of creating a duplicate role with an empty sector/track.
"""

from __future__ import annotations

import hashlib
import re
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path

from ..registry import Source
from .base import FetchResult, RawDoc

PAGE_URL = "https://www.skillsfuture.gov.sg/skills-framework/ict"


@dataclass
class Role:
    sector: str
    track: str
    title: str
    description: str = ""
    cwfs: OrderedDict[str, list[str]] = field(default_factory=OrderedDict)
    tsc: list[dict[str, str]] = field(default_factory=list)
    ccs: list[dict[str, str]] = field(default_factory=list)

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.sector, self.track, self.title)


def _norm(s) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(s or "").lower()).strip()


def _cell(v) -> str:
    return re.sub(r"\s+", " ", str(v)).strip() if v is not None else ""


# header-name matchers (normalized substring tests, first match wins)
_COLS = {
    "sector": lambda h: h == "sector",
    "track": lambda h: h == "track" or h.startswith("track "),
    "role": lambda h: h in ("job role", "job role title", "job roles") or h == "role",
    "description": lambda h: (
        "job role description" in h or h == "description" or "role description" in h
    ),
    "cwf": lambda h: "critical work function" in h,
    "task": lambda h: "key task" in h,
    "tsc_cat": lambda h: ("tsc" in h or "technical skill" in h) and "categor" in h,
    "tsc_title": lambda h: (
        ("tsc" in h or "technical skill" in h)
        and ("title" in h or h in ("tsc", "technical skills and competencies"))
    ),
    "ccs_title": lambda h: (
        ("ccs" in h or "critical core" in h or "generic skill" in h)
        and ("title" in h or h in ("ccs", "critical core skills", "generic skills"))
    ),
    "level": lambda h: "proficiency" in h and "description" not in h,
}


_LEVEL_COL_RE = re.compile(r"^(?:tsc |proficiency )?level (\d)$")


def _find_header(rows: list[list[str]]) -> tuple[int, dict[str, int]] | None:
    for idx, row in enumerate(rows[:20]):
        norm = [_norm(c) for c in row]
        if not any(n in ("job role", "job role title", "role", "job roles") for n in norm):
            continue
        cols: dict[str, int] = {}
        for name, test in _COLS.items():
            for ci, h in enumerate(norm):
                if h and test(h) and ci not in cols.values():
                    cols[name] = ci
                    break
        if "role" in cols:
            for ci, h in enumerate(norm):
                m = _LEVEL_COL_RE.match(h)
                if m:
                    cols[f"level_{m.group(1)}"] = ci
            return idx, cols
    return None


def classify_sheet(cols: dict[str, int]) -> str | None:
    if "cwf" in cols or "task" in cols or "description" in cols:
        return "description"
    if "tsc_title" in cols:
        return "tsc"
    if "ccs_title" in cols:
        return "ccs"
    return None


def parse_workbook(path: Path) -> list[Role]:
    import openpyxl

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    roles: OrderedDict[tuple[str, str, str], Role] = OrderedDict()

    def get_role(sector: str, track: str, title: str) -> Role:
        if not sector and not track:
            # no sector/track on this row: attach to an existing role of that title
            same = [r for r in roles.values() if r.title == title]
            if same:
                return same[0]
        k = (sector, track, title)
        if k not in roles:
            # a sheet processed earlier may have created the role without sector/track
            blank = roles.get(("", "", title))
            if blank is not None and (sector or track):
                blank.sector, blank.track = sector, track
                del roles[("", "", title)]
                roles[k] = blank
            else:
                roles[k] = Role(sector=sector, track=track, title=title)
        return roles[k]

    for ws in wb.worksheets:
        rows = [[_cell(c) for c in r] for r in ws.iter_rows(values_only=True)]
        found = _find_header(rows)
        if not found:
            continue
        hdr_idx, cols = found
        kind = classify_sheet(cols)
        if kind is None:
            continue
        last = {"sector": "", "track": "", "role": ""}
        last_cwf = ""
        for row in rows[hdr_idx + 1 :]:

            def g(name: str, cols: dict = cols, row: list = row) -> str:
                ci = cols.get(name)
                return row[ci] if ci is not None and ci < len(row) else ""

            if not any(row):
                continue
            for k in ("sector", "track", "role"):
                v = g(k)
                if v:
                    last[k] = v
            if not last["role"]:
                continue
            role = get_role(last["sector"], last["track"], last["role"])
            if kind == "description":
                if g("description") and not role.description:
                    role.description = g("description")
                cwf = g("cwf") or last_cwf
                if g("cwf"):
                    last_cwf = g("cwf")
                if cwf:
                    tasks = role.cwfs.setdefault(cwf, [])
                    if g("task") and g("task") not in tasks:
                        tasks.append(g("task"))
            elif kind == "tsc":
                title = g("tsc_title")
                if title:
                    level = g("level")
                    if not level:
                        marked = [
                            int(k.split("_")[1]) for k in cols if k.startswith("level_") and g(k)
                        ]
                        if marked:
                            level = f"Level {max(marked)}"
                    entry = {"title": title, "category": g("tsc_cat"), "level": level}
                    if entry not in role.tsc:
                        role.tsc.append(entry)
            elif kind == "ccs":
                title = g("ccs_title")
                if title:
                    entry = {"title": title, "level": g("level")}
                    if entry not in role.ccs:
                        role.ccs.append(entry)
    wb.close()
    return list(roles.values())


def role_to_text(r: Role) -> str:
    lines = [f"Job role: {r.title}", f"Sector: {r.sector}  |  Track: {r.track}".strip()]
    if r.description:
        lines += ["", "Role description:", r.description]
    if r.cwfs:
        lines += ["", "Critical work functions and key tasks:"]
        for cwf, tasks in r.cwfs.items():
            lines.append(f"- {cwf}")
            lines.extend(f"    * {t}" for t in tasks)
    if r.tsc:
        lines += [
            "",
            "Technical skills and competencies: " + "; ".join(sorted({t["title"] for t in r.tsc})),
        ]
    if r.ccs:
        lines += [
            "",
            "Generic / critical core skills: " + "; ".join(sorted({c["title"] for c in r.ccs})),
        ]
    return "\n".join(lines)


def roles_to_docs(roles: list[Role], file_name: str, version: str) -> list[RawDoc]:
    docs: list[RawDoc] = []
    for r in roles:
        text = role_to_text(r)
        slug = re.sub(r"[^a-z0-9]+", "-", f"{r.sector}-{r.track}-{r.title}".lower()).strip("-")
        docs.append(
            RawDoc(
                path=f"{file_name}#{slug}",
                text=text,
                doc_version=version,
                source_url=PAGE_URL,
                title=r.title,
                extra={
                    "heading_path": [x for x in (r.sector, r.track, r.title) if x],
                    "role_family": r.track,
                    "sector": r.sector,
                    "tsc_titles": sorted({t["title"] for t in r.tsc}),
                    "metadata": {
                        "sector": r.sector,
                        "role_family": r.track,
                        "job_role": r.title,
                        "tsc": r.tsc,  # [{title, category, level}]
                        "ccs": r.ccs,  # [{title, level}]
                        "critical_work_functions": list(r.cwfs.keys()),
                        "source_file": file_name,
                    },
                },
            )
        )
    return docs


def fetch(source: Source, raw_dir: Path) -> FetchResult:
    parser = (source.options or {}).get("parser", "skillsfuture_ict")
    if parser != "skillsfuture_ict":
        reason = f"unknown xlsx parser {parser!r} for {source.name}"
        print(f"[fetch] {reason}")
        return FetchResult(source_name=source.name, skipped=True, skip_reason=reason)

    src_dir = raw_dir / source.name
    pattern = (source.options or {}).get("glob", "*.xlsx")
    files = sorted(p for p in src_dir.glob(pattern) if not p.name.startswith("~$"))
    if not files:
        reason = (
            f"MANUAL DOWNLOAD REQUIRED: no {pattern} in {src_dir}. Download the Skills Framework "
            "for "
            f"Infocomm Technology Excel from {source.url} (SkillsFuture Singapore) in a browser "
            "and place "
            f"it there. Not auto-downloaded: the page is not scriptable (JS shell / HTTP 403)."
        )
        print(f"[fetch] {reason}")
        return FetchResult(source_name=source.name, skipped=True, skip_reason=reason)

    docs: list[RawDoc] = []
    errors: list[str] = []
    notes: list[str] = []
    seen = set()
    for f in files:
        try:
            roles = parse_workbook(f)
        except Exception as e:  # corrupt/unsupported workbook
            errors.append(f"{f.name}: could not parse workbook ({e})")
            continue
        if not roles:
            msg = (
                f"{f.name}: workbook parsed but yielded 0 job roles — no sheet with a recognisable "
                "'Job Role' header row (Sector/Track/Job Role/...); check the sheet layout against "
                "rag/loaders/xlsx_loader.py"
            )
            errors.append(msg)
            print(f"[fetch] WARNING {msg}")
            continue
        version = f"{f.name}@sha1:{hashlib.sha1(f.read_bytes()).hexdigest()[:10]}"
        new = [r for r in roles if r.key not in seen]
        seen.update(r.key for r in roles)
        docs.extend(roles_to_docs(new, f.name, version))
        notes.append(f"{f.name}: {len(roles)} job role(s) parsed")
    if not docs and errors:
        return FetchResult(
            source_name=source.name, skipped=True, skip_reason="; ".join(errors), errors=errors
        )
    print(f"[fetch] {source.name}: {len(docs)} job roles from {len(files)} workbook(s)")
    return FetchResult(source_name=source.name, docs=docs, errors=errors, notes=notes)
