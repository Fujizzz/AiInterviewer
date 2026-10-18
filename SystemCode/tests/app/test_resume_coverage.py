"""Coverage repair must recover omitted experiences without inventing source groups."""

import pytest

from app.parsing.resume import ResumeExtraction, experience_blocks, parse_resume_profile

TEXT = """Education
Example University
Internship Experience
SearchCo — Engineer Intern
• Built an evaluation dataset.
VideoCo — Algorithm Intern
• Implemented video memory monitoring.
Projects
InterviewLab — Interview Application
• Built a planner and executor.
Skills & Certifications
Python
"""


@pytest.mark.asyncio
async def test_missing_experience_repaired_with_original_source_refs():
    seen = []

    def llm(prompt, data, schema):
        seen.append(data)
        blocks = data["source_blocks"]
        chosen = blocks[-1:] if len(seen) == 1 else blocks
        return ResumeExtraction.model_validate(
            {
                "projects": [
                    {
                        "name": block["header"].split(" — ")[0],
                        "claims": [block["text"].split("\n")[1]],
                        "source_refs": [block["block_id"]],
                    }
                    for block in chosen
                ]
            }
        )

    profile, _ = await parse_resume_profile(TEXT, llm=llm)
    assert len(profile.projects) == 3
    assert set(profile.source_coverage.values()) == {"mapped"}
    assert len(seen) == 2
    assert "InterviewLab" not in seen[1]["resume_text"]
    assert all(
        project.source_refs and project.claims[0].source_refs for project in profile.projects
    )


@pytest.mark.asyncio
async def test_failed_coverage_repair_keeps_uncertainty_visible():
    count = 0

    def llm(prompt, data, schema):
        nonlocal count
        count += 1
        if count == 2:
            raise TimeoutError()
        return ResumeExtraction.model_validate(
            {
                "skills": ["Python"],
                "projects": [{"name": "InterviewLab", "claims": ["Built a planner and executor."]}],
            }
        )

    profile, _ = await parse_resume_profile(TEXT, llm=llm)
    assert list(profile.source_coverage.values()).count("uncertain") == 2
    assert len(profile.projects) == 1


@pytest.mark.asyncio
async def test_project_identity_does_not_depend_on_model_order():
    reverse = False

    def llm(prompt, data, schema):
        blocks = data["source_blocks"]
        projects = [{"name": b["header"], "source_refs": [b["block_id"]]} for b in blocks]
        return ResumeExtraction.model_validate(
            {"projects": projects[::-1] if reverse else projects}
        )

    first, _ = await parse_resume_profile(TEXT, llm=llm)
    reverse = True
    second, _ = await parse_resume_profile(TEXT, llm=llm)
    assert {p.name: p.project_id for p in first.projects} == {
        p.name: p.project_id for p in second.projects
    }


def test_source_blocks_preserve_internships_and_project_grouping():
    blocks = experience_blocks(TEXT)
    assert len(blocks) == 3
    assert "memory monitoring" in blocks[1]["text"]
    assert "Python" not in blocks[2]["text"]
