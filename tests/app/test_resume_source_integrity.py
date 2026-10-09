"""Source coverage must not be satisfied by arbitrary IDs or duplicate projects."""

import pytest

from app.parsing.resume import ResumeExtraction, experience_blocks, parse_resume_profile
from tests.app.test_resume_coverage import TEXT


def extracted(block):
    return {
        "name": block["header"].split(" — ")[0],
        "claims": [block["text"].split("\n")[-1].lstrip("• ")],
        "source_refs": [block["block_id"]],
    }


@pytest.mark.asyncio
async def test_citing_all_valid_block_ids_does_not_hide_omitted_experiences():
    calls = []

    def llm(prompt, data, schema):
        calls.append(data)
        blocks = data["source_blocks"]
        if len(calls) == 1:
            project = extracted(blocks[-1])
            project["source_refs"] = [block["block_id"] for block in blocks]
            return ResumeExtraction(projects=[project])
        return ResumeExtraction(projects=[extracted(block) for block in blocks])

    profile, _ = await parse_resume_profile(TEXT, llm=llm)
    assert {project.name for project in profile.projects} == {"SearchCo", "VideoCo", "InterviewLab"}
    assert len(calls) == 2
    assert all(len(project.source_refs) == 1 for project in profile.projects)


@pytest.mark.asyncio
async def test_duplicate_repair_projects_share_one_original_experience_identity():
    count = 0

    def llm(prompt, data, schema):
        nonlocal count
        count += 1
        blocks = data["source_blocks"]
        if count == 1:
            return ResumeExtraction(projects=[extracted(blocks[-1])])
        projects = [extracted(block) for block in blocks]
        return ResumeExtraction(projects=projects + projects)

    profile, _ = await parse_resume_profile(TEXT, llm=llm)
    assert len(profile.projects) == 3
    assert len({project.project_id for project in profile.projects}) == 3


@pytest.mark.asyncio
async def test_same_original_source_keeps_identity_under_model_name_paraphrase():
    alternate = False

    def llm(prompt, data, schema):
        projects = [extracted(block) for block in data["source_blocks"]]
        if alternate:
            for project in projects:
                project["name"] += " Engineering Experience"
        return ResumeExtraction(projects=projects)

    original, _ = await parse_resume_profile(TEXT, llm=llm)
    alternate = True
    paraphrased, _ = await parse_resume_profile(TEXT, llm=llm)
    assert {tuple(p.source_refs): p.project_id for p in original.projects} == {
        tuple(p.source_refs): p.project_id for p in paraphrased.projects
    }


@pytest.mark.asyncio
async def test_duplicate_source_references_cannot_change_project_identity():
    duplicate = False

    def llm(prompt, data, schema):
        projects = [extracted(block) for block in data["source_blocks"]]
        if duplicate:
            for project in projects:
                project["source_refs"] *= 2
        return ResumeExtraction(projects=projects)

    first, _ = await parse_resume_profile(TEXT, llm=llm)
    duplicate = True
    second, _ = await parse_resume_profile(TEXT, llm=llm)
    assert {p.name: p.project_id for p in first.projects} == {
        p.name: p.project_id for p in second.projects
    }
    assert all(len(project.source_refs) == 1 for project in second.projects)


@pytest.mark.asyncio
async def test_unsupported_project_with_forged_reference_is_not_published():
    def llm(prompt, data, schema):
        projects = [extracted(block) for block in data["source_blocks"]]
        projects.append(
            {
                "name": "Invented Space Mission",
                "claims": ["Built a lunar lander."],
                "source_refs": ["source-not-in-the-resume"],
            }
        )
        return ResumeExtraction(projects=projects)

    profile, _ = await parse_resume_profile(TEXT, llm=llm)
    assert "Invented Space Mission" not in {project.name for project in profile.projects}


def test_title_then_bullet_layout_keeps_distinct_experiences():
    text = """Projects
Cache Service
• Built an LRU cache.
Metrics Dashboard
• Tracked request rates.
Skills
Python
"""
    blocks = experience_blocks(text)
    assert len(blocks) == 2
    assert "Metrics Dashboard" not in blocks[0]["text"]


@pytest.mark.asyncio
async def test_unrecognized_layout_retains_explicit_uncertainty_without_extra_model_call():
    count = 0

    def llm(prompt, data, schema):
        nonlocal count
        count += 1
        return ResumeExtraction(
            skills=["Python"],
            projects=[{"name": "Cache Service", "claims": ["Built an LRU cache."]}],
        )

    profile, _ = await parse_resume_profile(
        "Professional Journey\nCache Service\nBuilt an LRU cache.",
        llm=llm,
    )
    assert profile.source_coverage
    assert "uncertain" in profile.source_coverage.values()
    assert count == 1
