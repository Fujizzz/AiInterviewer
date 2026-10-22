"""Responsibilities: Construct explicit offline canonical resume outputs for integration tests.
Implementation: Use supplied source lines for synthetic section/evidence coordinates and let callers
provide semantic facts. This helper never runs in production.
Related Modules: Backend/CLI model fixtures and source-integrity tests use the real shared schema.
Declaration Index:
- resume_output: Build one schema-valid synthetic output with explicit source coverage.
Variable Index:
None
Constraints:
Synthetic coordinates are fixture choices, not a production classifier or quality claim.
"""

from shared.resume_contract import ResumeExtraction, ResumeFacts, ResumeSectionLines


def resume_output(data, **values):
    """Functionality: Complete fixture-only section and evidence metadata.
    Inputs: Production-style model request data and explicit synthetic semantic fields.
    Outputs: ResumeExtraction validated with the actual shared types.
    Logic: Preserve explicit coordinates; for a single unspecified fixture project use all nonblank
    lines. Assign cited lines to projects and the rest to other, unless a section map is supplied.
    Attach the first nonblank source line as synthetic evidence for known fixture facts.
    Constraints: No model, database or filesystem access; no claim of factual model grounding.
    """
    lines = [n for n, line in enumerate(data["resume_text"].splitlines(), 1) if line.strip()]
    projects = [dict(project) for project in values.get("projects", [])]
    for project in projects:
        if "source_lines" not in project:
            assert len(projects) == 1, "Multi-project fixtures require explicit source lines"
            project["source_lines"] = lines
    values["projects"] = projects
    cited = {n for project in projects for n in project["source_lines"]}
    sections = (
        data.get("authored_sections")
        or ResumeSectionLines(
            projects=sorted(cited & set(lines)), other=[n for n in lines if n not in cited]
        ).model_dump()
    )
    evidence = {key: lines[:1] for key in ResumeFacts.model_fields if values.get(key) is not None}
    return ResumeExtraction.model_validate(
        {
            "section_lines": sections,
            "field_evidence": evidence,
            **values,
        }
    )
