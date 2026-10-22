"""Responsibilities: Source coverage must not be satisfied by arbitrary IDs or duplicate projects.
Implementation: Execute typed provider or application paths with explicit validation boundaries.
Related Modules: shared.resume_contract and app.parsing.resume define canonical resume data.
Declaration Index:
- extracted: Build one deterministic source-derived fixture.
- test_source_section_coverage_does_not_hide_omitted_experiences: Reject incomplete experience
  ownership despite complete section coverage.
- test_source_section_coverage_does_not_hide_omitted_experiences.llm: Supply the enclosing
  test scenario through the real canonical schema.
- test_duplicate_projects_share_one_original_experience_identity: Verify duplicate projects
  share one original experience identity.
- test_duplicate_projects_share_one_original_experience_identity.llm: Supply the enclosing test
  scenario through the real canonical schema.
- test_same_original_source_keeps_identity_under_model_name_paraphrase: Verify same original
  source keeps identity under model name paraphrase.
- test_same_original_source_keeps_identity_under_model_name_paraphrase.llm: Supply the enclosing
  test scenario through the real canonical schema.
- test_duplicate_source_lines_are_rejected: Verify duplicate source lines fail explicitly.
- test_duplicate_source_lines_are_rejected.llm: Supply the enclosing test
  scenario through the real canonical schema.
- test_unsupported_project_with_forged_reference_is_not_published: Verify unsupported project
  with forged reference is not published.
- test_unsupported_project_with_forged_reference_is_not_published.llm: Supply the enclosing test
  scenario through the real canonical schema.
- test_title_then_bullet_layout_keeps_distinct_experiences: Verify title then bullet layout
  keeps distinct experiences.
- test_unrecognized_layout_retains_original_without_extra_model_call: Verify
  unrecognized layout retains original text without extra model call.
- test_unrecognized_layout_retains_original_without_extra_model_call.llm: Supply the
  enclosing test scenario through the real canonical schema.
Variable Index:
None
"""

import pytest

from app.parsing.resume import experience_blocks, parse_resume_profile
from tests.app.resume_fixture import resume_output
from tests.app.test_resume_coverage import TEXT


def extracted(block):
    """Functionality: Build one deterministic source-derived fixture.
    Inputs: Source block or mocked PDF arguments as declared. Outputs: Scripted extraction data.
    Logic: Preserve the test source identity and content. Constraints: No real model or PDF parsing.
    """
    return {
        "name": block["header"].split(" — ")[0],
        "claims": [block["text"].split("\n")[-1].lstrip("• ")],
        "source_lines": block["line_numbers"],
    }


@pytest.mark.asyncio
async def test_source_section_coverage_does_not_hide_omitted_experiences():
    """Functionality: Verify section coverage does not hide omitted experiences.
    Inputs: Isolated test fixtures and explicit provider substitutes. Outputs: Assertions or test
    failure.
    Logic: Exercise the real contract under the stated boundary conditions.
    Constraints: This test does not measure live model quality or modify production records.
    """
    calls = []

    def llm(prompt, data, schema):
        """Functionality: Supply the enclosing test scenario through the real canonical schema.
        Inputs: prompt, numbered source data and schema. Outputs: Scripted ResumeExtraction.
        Logic: Select or alter explicit source blocks according to the test state.
        Constraints: Offline model replacement only; production validation remains active.
        """
        calls.append(data)
        blocks = data["source_blocks"]
        return resume_output(data, projects=[extracted(blocks[-1])])

    with pytest.raises(ValueError, match="resume_experience_coverage_incomplete"):
        await parse_resume_profile(TEXT, llm=llm)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_duplicate_projects_share_one_original_experience_identity():
    """Functionality: Verify duplicate projects share one original experience identity.
    Inputs: Isolated test fixtures and explicit provider substitutes. Outputs: Assertions or test
    failure.
    Logic: Exercise the real contract under the stated boundary conditions.
    Constraints: This test does not measure live model quality or modify production records.
    """
    count = 0

    def llm(prompt, data, schema):
        """Functionality: Supply the enclosing test scenario through the real canonical schema.
        Inputs: prompt, numbered source data and schema. Outputs: Scripted ResumeExtraction.
        Logic: Select or alter explicit source blocks according to the test state.
        Constraints: Offline model replacement only; production validation remains active.
        """
        nonlocal count
        count += 1
        blocks = data["source_blocks"]
        projects = [extracted(block) for block in blocks]
        return resume_output(data, projects=projects + projects)

    profile, _ = await parse_resume_profile(TEXT, llm=llm)
    assert len(profile.projects) == 3
    assert len({project.project_id for project in profile.projects}) == 3


@pytest.mark.asyncio
async def test_same_original_source_keeps_identity_under_model_name_paraphrase():
    """Functionality: Verify same original source keeps identity under model name paraphrase.
    Inputs: Isolated test fixtures and explicit provider substitutes. Outputs: Assertions or test
    failure.
    Logic: Exercise the real contract under the stated boundary conditions.
    Constraints: This test does not measure live model quality or modify production records.
    """
    alternate = False

    def llm(prompt, data, schema):
        """Functionality: Supply the enclosing test scenario through the real canonical schema.
        Inputs: prompt, numbered source data and schema. Outputs: Scripted ResumeExtraction.
        Logic: Select or alter explicit source blocks according to the test state.
        Constraints: Offline model replacement only; production validation remains active.
        """
        projects = [extracted(block) for block in data["source_blocks"]]
        if alternate:
            for project in projects:
                project["name"] += " Engineering Experience"
        return resume_output(data, projects=projects)

    original, _ = await parse_resume_profile(TEXT, llm=llm)
    alternate = True
    paraphrased, _ = await parse_resume_profile(TEXT, llm=llm)
    assert {tuple(p.source_refs): p.project_id for p in original.projects} == {
        tuple(p.source_refs): p.project_id for p in paraphrased.projects
    }


@pytest.mark.asyncio
async def test_duplicate_source_lines_are_rejected():
    """Functionality: Verify duplicate source lines fail explicitly.
    Inputs: Isolated test fixtures and explicit provider substitutes. Outputs: Assertions or test
    failure.
    Logic: Exercise the real contract under the stated boundary conditions.
    Constraints: This test does not measure live model quality or modify production records.
    """
    duplicate = False

    def llm(prompt, data, schema):
        """Functionality: Supply the enclosing test scenario through the real canonical schema.
        Inputs: prompt, numbered source data and schema. Outputs: Scripted ResumeExtraction.
        Logic: Select or alter explicit source blocks according to the test state.
        Constraints: Offline model replacement only; production validation remains active.
        """
        projects = [extracted(block) for block in data["source_blocks"]]
        if duplicate:
            for project in projects:
                project["source_lines"] *= 2
        return resume_output(data, projects=projects)

    await parse_resume_profile(TEXT, llm=llm)
    duplicate = True
    with pytest.raises(ValueError, match="resume_project_source_invalid"):
        await parse_resume_profile(TEXT, llm=llm)


@pytest.mark.asyncio
async def test_unsupported_project_with_forged_reference_is_not_published():
    """Functionality: Verify unsupported project with forged reference is not published.
    Inputs: Isolated test fixtures and explicit provider substitutes. Outputs: Assertions or test
    failure.
    Logic: Exercise the real contract under the stated boundary conditions.
    Constraints: This test does not measure live model quality or modify production records.
    """

    def llm(prompt, data, schema):
        """Functionality: Supply the enclosing test scenario through the real canonical schema.
        Inputs: prompt, numbered source data and schema. Outputs: Scripted ResumeExtraction.
        Logic: Select or alter explicit source blocks according to the test state.
        Constraints: Offline model replacement only; production validation remains active.
        """
        projects = [extracted(block) for block in data["source_blocks"]]
        projects.append(
            {
                "name": "Invented Space Mission",
                "claims": ["Built a lunar lander."],
                "source_lines": [99],
            }
        )
        return resume_output(data, projects=projects)

    with pytest.raises(ValueError, match="resume_project_source_invalid"):
        await parse_resume_profile(TEXT, llm=llm)


def test_title_then_bullet_layout_keeps_distinct_experiences():
    """Functionality: Verify title then bullet layout keeps distinct experiences.
    Inputs: Isolated test fixtures and explicit provider substitutes. Outputs: Assertions or test
    failure.
    Logic: Exercise the real contract under the stated boundary conditions.
    Constraints: This test does not measure live model quality or modify production records.
    """
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
async def test_unrecognized_layout_retains_original_without_extra_model_call():
    """Functionality: Verify unrecognized layout retains original text without extra model
    call.
    Inputs: Isolated test fixtures and explicit provider substitutes. Outputs: Assertions or test
    failure.
    Logic: Exercise the real contract under the stated boundary conditions.
    Constraints: This test does not measure live model quality or modify production records.
    """
    count = 0

    def llm(prompt, data, schema):
        """Functionality: Supply the enclosing test scenario through the real canonical schema.
        Inputs: prompt, numbered source data and schema. Outputs: Scripted ResumeExtraction.
        Logic: Select or alter explicit source blocks according to the test state.
        Constraints: Offline model replacement only; production validation remains active.
        """
        nonlocal count
        count += 1
        return resume_output(
            data,
            skills=["Python"],
            projects=[{"name": "Cache Service", "claims": ["Built an LRU cache."]}],
        )

    profile, _ = await parse_resume_profile(
        "Professional Journey\nCache Service\nBuilt an LRU cache.",
        llm=llm,
    )
    assert profile.source_coverage
    assert set(profile.source_coverage.values()) == {"mapped"}
    assert (
        "Professional Journey\nCache Service\nBuilt an LRU cache."
        in profile.projects[0].description
    )
    assert count == 1
