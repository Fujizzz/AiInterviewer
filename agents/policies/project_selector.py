"""Deterministic project selection for a fixed target competency."""

from __future__ import annotations

import re

from agents.config import AgentSettings, load_agent_settings
from agents.domain.models import ProjectSelection
from shared.contracts import (
    CandidateProfile,
    CandidateProject,
    Competency,
    InterviewState,
    JobProfile,
)

_WORD_PATTERN = re.compile(r"[a-z0-9+#.]+")
_STOP_WORDS = {
    "a",
    "an",
    "and",
    "engineer",
    "engineering",
    "for",
    "in",
    "of",
    "the",
}

_COMPETENCY_HINTS: dict[Competency, frozenset[str]] = {
    Competency.TECHNICAL_DEPTH: frozenset(
        {
            "architecture",
            "cuda",
            "database",
            "distributed",
            "gpu",
            "implementation",
            "inference",
            "latency",
            "memory",
            "model",
            "optimization",
            "performance",
            "pytorch",
            "scaling",
            "serving",
            "system",
            "throughput",
        }
    ),
    Competency.OWNERSHIP: frozenset(
        {"built", "created", "designed", "implemented", "led", "migrated", "owned"}
    ),
    Competency.DECISION_MAKING: frozenset(
        {"architecture", "chose", "decision", "designed", "selected", "tradeoff"}
    ),
    Competency.DEBUGGING: frozenset(
        {
            "bug",
            "debugged",
            "failure",
            "fixed",
            "gpu",
            "latency",
            "memory",
            "oom",
            "root",
        }
    ),
    Competency.EVALUATION: frozenset(
        {"accuracy", "benchmark", "evaluated", "latency", "metric", "quality", "throughput"}
    ),
    Competency.ADAPTABILITY: frozenset(
        {"constraint", "load", "migrated", "scale", "scaled", "scaling", "traffic"}
    ),
}


class ProjectSelector:
    """Rank candidate projects using configured, explainable heuristics."""

    def __init__(self, settings: AgentSettings | None = None) -> None:
        self._settings = settings or load_agent_settings()

    def select(
        self,
        *,
        profile: CandidateProfile,
        job: JobProfile,
        state: InterviewState,
        competency: Competency,
    ) -> ProjectSelection:
        if not profile.projects:
            raise ValueError("ProjectSelector requires at least one candidate project")

        config = self._settings.project_selector
        all_scores: dict[str, float] = {}
        for project in profile.projects:
            score = (
                config.competency_relevance_weight * self._competency_relevance(project, competency)
                + config.job_relevance_weight * self._job_relevance(project, job)
                + config.unverified_claim_weight * self._unverified_claim_value(project, competency)
                - config.visit_penalty_weight * self._visit_penalty(project, state)
            )
            all_scores[project.project_id] = round(score, 6)

        selected = max(profile.projects, key=lambda project: all_scores[project.project_id])
        visits = state.project_visit_count.get(selected.project_id, 0)
        reason_code = (
            "RELEVANT_UNVISITED_PROJECT" if visits == 0 else "BEST_SCORE_AFTER_VISIT_PENALTY"
        )
        return ProjectSelection(
            project_id=selected.project_id,
            score=all_scores[selected.project_id],
            reason_code=reason_code,
            all_scores=all_scores,
        )

    def _competency_relevance(
        self,
        project: CandidateProject,
        competency: Competency,
    ) -> float:
        tokens = self._project_tokens(project)
        hint_hits = len(tokens & _COMPETENCY_HINTS[competency])
        hint_score = min(hint_hits / 3.0, 1.0)
        structure_score = min(
            0.12 * len(project.technologies)
            + 0.12 * len(project.claims)
            + 0.08 * len(project.metrics),
            1.0,
        )
        if competency == Competency.TECHNICAL_DEPTH:
            return min(0.65 * structure_score + 0.35 * hint_score, 1.0)
        return min(0.35 * structure_score + 0.65 * hint_score, 1.0)

    def _job_relevance(self, project: CandidateProject, job: JobProfile) -> float:
        project_tokens = self._project_tokens(project)
        job_text = " ".join([job.title, *job.domains])
        job_tokens = self._tokens(job_text)
        if not job_tokens:
            return 0.0
        overlap = len(project_tokens & job_tokens) / len(job_tokens)
        exact_domain = bool(
            project.domain
            and any(project.domain.casefold() == domain.casefold() for domain in job.domains)
        )
        return min(overlap + (0.6 if exact_domain else 0.0), 1.0)

    def _unverified_claim_value(
        self,
        project: CandidateProject,
        competency: Competency,
    ) -> float:
        if not project.claims:
            return 0.0
        hints = _COMPETENCY_HINTS[competency]
        relevant_claims = sum(1 for claim in project.claims if self._tokens(claim.text) & hints)
        return min((len(project.claims) + relevant_claims) / 4.0, 1.0)

    def _visit_penalty(self, project: CandidateProject, state: InterviewState) -> float:
        visits = max(0, state.project_visit_count.get(project.project_id, 0))
        return min(visits / self._settings.project_selector.visits_until_full_penalty, 1.0)

    @classmethod
    def _project_tokens(cls, project: CandidateProject) -> set[str]:
        text = " ".join(
            filter(
                None,
                [
                    project.name,
                    project.domain,
                    project.description,
                    *project.technologies,
                    *(claim.text for claim in project.claims),
                    *project.metrics,
                ],
            )
        )
        return cls._tokens(text)

    @staticmethod
    def _tokens(text: str) -> set[str]:
        return {
            token
            for token in _WORD_PATTERN.findall(text.casefold().replace("_", " "))
            if token not in _STOP_WORDS
        }
