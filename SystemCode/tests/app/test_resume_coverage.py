"""Responsibilities: Canonical extraction reports omitted experiences without a second model call.
Implementation: Execute typed provider or application paths with explicit validation boundaries.
Related Modules: shared.resume_contract and app.parsing.resume define canonical resume data.
Declaration Index:
- test_missing_experience_remains_explicit_without_repair: Verify missing experience remains
  explicit without repair.
- test_missing_experience_remains_explicit_without_repair.llm: Supply the enclosing test
  scenario through the real canonical schema.
- test_partial_output_fails_even_with_skills: Reject missing experiences even when skills exist.
- test_partial_output_fails_even_with_skills.llm: Supply the enclosing test scenario through
  the real canonical schema.
- test_project_identity_does_not_depend_on_model_order: Verify project identity does not depend
  on model order.
- test_project_identity_does_not_depend_on_model_order.llm: Supply the enclosing test scenario
  through the real canonical schema.
- test_source_blocks_preserve_internships_and_project_grouping: Verify source blocks preserve
  internships and project grouping.
Variable Index:
- TEXT: Synthetic multi-experience source for coverage and attribution tests.
"""

import pytest

from app.parsing.resume import experience_blocks, parse_resume_profile
from tests.app.resume_fixture import resume_output

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
async def test_missing_experience_remains_explicit_without_repair():
    """Functionality: Verify missing experience remains explicit without repair.
    Inputs: Isolated test fixtures and explicit provider substitutes. Outputs: Assertions or test
    failure.
    Logic: Exercise the real contract under the stated boundary conditions.
    Constraints: This test does not measure live model quality or modify production records.
    """
    seen = []

    def llm(prompt, data, schema):
        """Functionality: Supply the enclosing test scenario through the real canonical schema.
        Inputs: prompt, numbered source data and schema. Outputs: Scripted ResumeExtraction.
        Logic: Select or alter explicit source blocks according to the test state.
        Constraints: Offline model replacement only; production validation remains active.
        """
        seen.append(data)
        blocks = data["source_blocks"]
        chosen = blocks[-1:]
        return resume_output(
            data,
            **{
                "projects": [
                    {
                        "name": block["header"].split(" — ")[0],
                        "claims": [block["text"].split("\n")[1]],
                        "source_lines": block["line_numbers"],
                    }
                    for block in chosen
                ]
            },
        )

    with pytest.raises(ValueError, match="resume_experience_coverage_incomplete"):
        await parse_resume_profile(TEXT, llm=llm)
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_partial_output_fails_even_with_skills():
    """Functionality: Verify partial output fails even with skills, making omissions visible.
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
            **{
                "skills": ["Python"],
                "projects": [
                    {
                        "name": "InterviewLab",
                        "claims": ["Built a planner and executor."],
                        "source_lines": [9, 10],
                    }
                ],
            },
        )

    with pytest.raises(ValueError, match="resume_experience_coverage_incomplete"):
        await parse_resume_profile(TEXT, llm=llm)
    assert count == 1


@pytest.mark.asyncio
async def test_project_identity_does_not_depend_on_model_order():
    """Functionality: Verify project identity does not depend on model order.
    Inputs: Isolated test fixtures and explicit provider substitutes. Outputs: Assertions or test
    failure.
    Logic: Exercise the real contract under the stated boundary conditions.
    Constraints: This test does not measure live model quality or modify production records.
    """
    reverse = False

    def llm(prompt, data, schema):
        """Functionality: Supply the enclosing test scenario through the real canonical schema.
        Inputs: prompt, numbered source data and schema. Outputs: Scripted ResumeExtraction.
        Logic: Select or alter explicit source blocks according to the test state.
        Constraints: Offline model replacement only; production validation remains active.
        """
        blocks = data["source_blocks"]
        projects = [{"name": b["header"], "source_lines": b["line_numbers"]} for b in blocks]
        return resume_output(data, **{"projects": projects[::-1] if reverse else projects})

    first, _ = await parse_resume_profile(TEXT, llm=llm)
    reverse = True
    second, _ = await parse_resume_profile(TEXT, llm=llm)
    assert {p.name: p.project_id for p in first.projects} == {
        p.name: p.project_id for p in second.projects
    }


def test_source_blocks_preserve_internships_and_project_grouping():
    """Functionality: Verify source blocks preserve internships and project grouping.
    Inputs: Isolated test fixtures and explicit provider substitutes. Outputs: Assertions or test
    failure.
    Logic: Exercise the real contract under the stated boundary conditions.
    Constraints: This test does not measure live model quality or modify production records.
    """
    blocks = experience_blocks(TEXT)
    assert len(blocks) == 3
    assert "memory monitoring" in blocks[1]["text"]
    assert "Python" not in blocks[2]["text"]
