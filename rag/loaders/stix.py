"""STIX 2.x bundle -> documents (MITRE ATT&CK).

Only the human-readable parts are kept: one document per ATT&CK technique / sub-technique and
per tactic, built from `name`, `description`, tactics, platforms and the data-source / detection
text. Groups, software, campaigns, relationships and revoked / deprecated objects are skipped.
Nothing here needs the network: it is a pure function over the parsed bundle.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

_CITATION_RE = re.compile(r"\s*\(Citation:[^)]*\)")
_CODE_RE = re.compile(r"</?code>")
_LINK_RE = re.compile(r"\[([^\]]+)\]\((?:https?://|/)[^)]*\)")
_BR_RE = re.compile(r"<br\s*/?>")


@dataclass
class StixDoc:
    attack_id: str
    kind: str  # technique | subtechnique | tactic
    title: str
    text: str
    url: str


def _clean(desc: str) -> str:
    desc = _CITATION_RE.sub("", desc or "")
    desc = _CODE_RE.sub("`", desc)
    desc = _BR_RE.sub("\n", desc)
    desc = _LINK_RE.sub(r"\1", desc)
    return re.sub(r"\n{3,}", "\n\n", desc).strip()


def _external(obj: dict) -> tuple[str, str]:
    for ref in obj.get("external_references", []):
        if ref.get("source_name") == "mitre-attack" and ref.get("external_id"):
            return ref["external_id"], ref.get("url", "")
    return "", ""


def parse_bundle(raw: str | dict) -> list[StixDoc]:
    bundle = json.loads(raw) if isinstance(raw, str) else raw
    objs = bundle.get("objects", [])
    docs: list[StixDoc] = []
    for o in objs:
        if o.get("revoked") or o.get("x_mitre_deprecated"):
            continue
        typ = o.get("type")
        if typ not in ("attack-pattern", "x-mitre-tactic"):
            continue
        attack_id, url = _external(o)
        if not attack_id or not o.get("name"):
            continue
        desc = _clean(o.get("description", ""))
        if not desc:
            continue
        if typ == "x-mitre-tactic":
            kind = "tactic"
            body = f"# {attack_id} {o['name']} (ATT&CK tactic)\n\n{desc}\n"
        else:
            kind = "subtechnique" if o.get("x_mitre_is_subtechnique") else "technique"
            tactics = [
                p["phase_name"].replace("-", " ")
                for p in o.get("kill_chain_phases", [])
                if p.get("kill_chain_name") == "mitre-attack"
            ]
            lines = [f"# {attack_id} {o['name']} (ATT&CK {kind})", ""]
            if tactics:
                lines.append(f"**Tactics:** {', '.join(tactics)}")
            plats = o.get("x_mitre_platforms") or []
            if plats:
                lines.append(f"**Platforms:** {', '.join(plats)}")
            sources = o.get("x_mitre_data_sources") or []
            if sources:
                lines.append(f"**Data sources:** {', '.join(sources[:8])}")
            lines += ["", desc]
            det = _clean(o.get("x_mitre_detection", ""))
            if det:
                lines += ["", "## Detection", "", det]
            body = "\n".join(lines) + "\n"
        docs.append(StixDoc(attack_id, kind, f"{attack_id} {o['name']}", body, url))
    docs.sort(key=lambda d: d.attack_id)
    return docs
