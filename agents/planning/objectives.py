"""Controller-owned evidence floor and bounded fallback agenda selection."""

import re
from hashlib import sha256

from shared.contracts.planning import InformationNeed


def evidence_criteria(depth):
    criteria = "A concrete implementation mechanism, beyond naming personal responsibilities"
    if depth != "brief":
        criteria += "; evidence of how its behavior was validated, including limitations"
    if depth == "deep":
        criteria += "; one consequential design trade-off or failure boundary"
    return criteria


def prepare_objectives(proposal, existing):
    """Preserved goals keep their exact evidence contract; new goals have a depth floor."""
    for item in proposal.topics:
        if item.topic_key in existing and item.objective_change == "preserve":
            continue
        floor = evidence_criteria(item.depth)
        if floor not in item.completion_criteria:
            item.completion_criteria = (
                item.completion_criteria[: 400 - len(floor) - 2] + "; " + floor
            )


def seed_entry_need(progress, topic_key, label):
    """Bind the first executor intent to a mechanism, before any answer exists."""
    if progress.questions_asked or progress.information_needs:
        return
    target = f"Concrete implementation mechanism within {label}"[:400]
    need = InformationNeed(
        need_id="need:"
        + sha256(f"{topic_key}:{progress.objective_version}:entry".encode()).hexdigest()[:20],
        objective_id=progress.objective_id or topic_key,
        objective_version=progress.objective_version,
        target=target,
        answer_unit=target,
    )
    progress.information_needs.append(need)
    progress.next_need_id = need.need_id


def fallback_order(context, eligible):
    job = context.job_profile.title.casefold()
    terms = set(re.findall(r"\w+", job)) - {"engineer", "developer", "senior", "junior"}
    if {"ai", "ml"} & terms or "人工智能" in job:
        terms.update(
            {
                "rag",
                "llm",
                "agent",
                "embedding",
                "pytorch",
                "transformer",
                "machine learning",
                "大模型",
                "检索",
                "智能体",
            }
        )
    projects = {p.project_id: p for p in context.candidate_profile.projects}

    def score(item):
        project = projects.get(item["project_id"])
        source = (
            " ".join(
                [item["label"], project.name, project.description or "", *project.technologies]
            ).casefold()
            if project
            else item["label"].casefold()
        )
        relevance = sum(
            bool(re.search(r"(?<!\w)" + re.escape(t) + r"(?!\w)", source)) for t in terms
        )
        # Prefer substantive claims over isolated technology names and numeric metrics.
        return (relevance, ":claim:" in item["topic_key"], len(item["label"]))

    ranked = sorted(eligible, key=lambda key: score(eligible[key]), reverse=True)
    owners, order = set(), []
    for key in ranked:
        owner = eligible[key]["project_id"]
        if owner not in owners:
            owners.add(owner)
            order.append(key)
    return order
