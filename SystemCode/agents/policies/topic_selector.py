"""Resume-grounded topics with stable keys retained for the full interview."""

import re

from agents.domain.models import TopicSelection
from shared.contracts import CandidateProject


class TopicSelector:
    def candidates(self, project: CandidateProject) -> list[TopicSelection]:
        topics = []
        seen = set()
        values = [(f"claim:{c.claim_id}", c.text, c.claim_id) for c in project.claims]
        values += [(f"technology:{t.casefold()}", t, None) for t in project.technologies]
        values += [(f"metric:{m.casefold()}", m, None) for m in project.metrics]
        if not values:
            values = [("project", project.name, None)]
        for key, text, claim_id in values:
            normalized = " ".join(re.findall(r"\w+", text.casefold()))
            if not normalized or any(normalized in previous for previous in seen):
                continue
            if any(marker in normalized for marker in ("ignore previous", "system prompt")):
                continue
            seen.add(normalized)
            topics.append(
                TopicSelection(
                    topic=text,
                    topic_key=f"{project.project_id}:{key}",
                    source_claim_id=claim_id,
                    reason_code="UNEXPLORED_RESUME_FACT",
                )
            )
        return topics

    def select(self, *, project: CandidateProject, used_topic_keys=()) -> TopicSelection | None:
        return next(
            (t for t in self.candidates(project) if t.topic_key not in used_topic_keys), None
        )
