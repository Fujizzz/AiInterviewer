"""Choose resume projects by job relevance and available detail, never by competency."""

import re

from agents.config import AgentSettings, load_agent_settings
from agents.domain.models import ProjectSelection
from shared.contracts import CandidateProfile, InterviewState, JobProfile


class ProjectSelector:
    def __init__(self, settings: AgentSettings | None = None):
        self._settings = settings or load_agent_settings()

    def select(
        self,
        *,
        profile: CandidateProfile,
        job: JobProfile,
        state: InterviewState,
        excluded_project_ids=(),
    ) -> ProjectSelection:
        projects = [p for p in profile.projects if p.project_id not in excluded_project_ids]
        if not projects:
            raise ValueError("No available project")
        job_tokens = set(re.findall(r"\w+", " ".join([job.title, *job.domains]).casefold()))
        scores = {}
        rules = self._settings.project_selector
        for project in projects:
            text = " ".join(
                [
                    project.name,
                    project.description or "",
                    project.domain or "",
                    *project.technologies,
                ]
            )
            tokens = set(re.findall(r"\w+", text.casefold()))
            relevance = len(tokens & job_tokens) / max(1, len(job_tokens))
            detail = min(1, (len(project.claims) + len(project.technologies)) / 6)
            scores[project.project_id] = (
                rules.job_relevance_weight * relevance
                + rules.detail_weight * detail
                - rules.visit_penalty_weight * state.project_visit_count.get(project.project_id, 0)
            )
        selected = max(projects, key=lambda p: scores[p.project_id])
        return ProjectSelection(
            project_id=selected.project_id,
            score=scores[selected.project_id],
            reason_code="JOB_RELEVANCE_AND_UNEXPLORED_WORK",
            all_scores=scores,
        )
