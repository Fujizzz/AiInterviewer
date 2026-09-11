"""Deterministic topic selection from candidate-provided project data."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence

from agents.domain.models import TopicSelection
from shared.contracts import CandidateClaim, CandidateProject, Competency

_INJECTION_MARKERS = (
    "ignore previous",
    "ignore all",
    "system prompt",
    "developer message",
    "ask me an easy",
)

_CANONICAL_TOPICS: tuple[tuple[str, str], ...] = (
    ("gpu memory", "GPU memory"),
    ("gpu_memory", "GPU memory"),
    ("out of memory", "GPU memory"),
    ("oom", "GPU memory"),
    ("multi-gpu", "multi-GPU inference"),
    ("multi gpu", "multi-GPU inference"),
    ("throughput", "throughput"),
    ("latency", "latency"),
    ("utilization", "resource utilization"),
    ("accuracy", "accuracy"),
    ("quality", "quality metrics"),
    ("scal", "scaling"),
    ("database", "database"),
    ("cache", "caching"),
    ("frontend", "frontend architecture"),
)

_COMPETENCY_TOPIC_HINTS: dict[Competency, tuple[str, ...]] = {
    Competency.TECHNICAL_DEPTH: ("mechanism", "architecture", "memory", "inference"),
    Competency.OWNERSHIP: ("implemented", "built", "designed", "owned"),
    Competency.DECISION_MAKING: ("architecture", "design", "tradeoff", "selected"),
    Competency.DEBUGGING: ("memory", "latency", "failure", "bug", "oom"),
    Competency.EVALUATION: ("metric", "latency", "throughput", "accuracy", "quality"),
    Competency.ADAPTABILITY: ("scale", "throughput", "load", "constraint", "memory"),
}


class TopicSelector:
    """Choose a stable topic; retrieval happens only after this decision."""

    def select(
        self,
        *,
        project: CandidateProject,
        competency: Competency,
        recent_topics: Sequence[str] = (),
        candidate_claims: Sequence[CandidateClaim] | None = None,
        excluded_topics: Iterable[str] = (),
    ) -> TopicSelection:
        candidates = self._ranked_candidates(
            project=project,
            competency=competency,
            candidate_claims=candidate_claims,
        )
        excluded = {topic.casefold() for topic in excluded_topics}
        recent = {topic.casefold() for topic in recent_topics}
        available = [
            candidate for candidate in candidates if candidate.topic.casefold() not in excluded
        ]
        if not available:
            return TopicSelection(topic=project.name, reason_code="PROJECT_NAME_FALLBACK")
        return max(
            available,
            key=lambda candidate: (
                candidate.topic.casefold() not in recent,
                self._topic_fit(candidate.topic, competency),
                candidate.source_claim_id is not None,
            ),
        )

    def select_alternate(
        self,
        *,
        project: CandidateProject,
        competency: Competency,
        excluded_topics: Iterable[str],
        candidate_claims: Sequence[CandidateClaim] | None = None,
    ) -> TopicSelection | None:
        excluded = {topic.casefold() for topic in excluded_topics}
        candidates = self._ranked_candidates(
            project=project,
            competency=competency,
            candidate_claims=candidate_claims,
        )
        for candidate in sorted(
            candidates,
            key=lambda item: (
                self._topic_fit(item.topic, competency),
                item.source_claim_id is not None,
            ),
            reverse=True,
        ):
            if candidate.topic.casefold() not in excluded:
                return candidate
        return None

    def _ranked_candidates(
        self,
        *,
        project: CandidateProject,
        competency: Competency,
        candidate_claims: Sequence[CandidateClaim] | None,
    ) -> list[TopicSelection]:
        candidates: list[TopicSelection] = []
        claims = candidate_claims if candidate_claims is not None else project.claims
        for claim in claims:
            if any(marker in claim.text.casefold() for marker in _INJECTION_MARKERS):
                continue
            topic = self._extract_topic(claim.text)
            if topic:
                candidates.append(
                    TopicSelection(
                        topic=topic,
                        source_claim_id=claim.claim_id,
                        reason_code="COMPETENCY_RELEVANT_CLAIM",
                    )
                )
        for metric in project.metrics:
            candidates.append(
                TopicSelection(
                    topic=self._extract_topic(metric) or metric.replace("_", " "),
                    reason_code="PROJECT_METRIC",
                )
            )
        for technology in project.technologies:
            candidates.append(TopicSelection(topic=technology, reason_code="PROJECT_TECHNOLOGY"))
        if not candidates:
            candidates.append(
                TopicSelection(topic=project.name, reason_code="PROJECT_NAME_FALLBACK")
            )

        deduplicated: dict[str, TopicSelection] = {}
        for candidate in candidates:
            deduplicated.setdefault(candidate.topic.casefold(), candidate)
        return list(deduplicated.values())

    @staticmethod
    def _extract_topic(text: str) -> str | None:
        normalized = text.casefold()
        for marker, topic in _CANONICAL_TOPICS:
            if marker in normalized:
                return topic
        words = re.findall(r"[A-Za-z0-9+#.-]+", text)
        content_words = [
            word
            for word in words
            if word.casefold()
            not in {
                "a",
                "an",
                "and",
                "implemented",
                "improved",
                "reduced",
                "the",
                "usage",
            }
        ]
        return " ".join(content_words[:3]) or None

    @staticmethod
    def _topic_fit(topic: str, competency: Competency) -> int:
        normalized = topic.casefold()
        return sum(hint in normalized for hint in _COMPETENCY_TOPIC_HINTS[competency])
