"""Responsibilities: Verify line-level completeness and lossless adaptation into Agent contracts.
Implementation: Feed explicit source assignments through the real parser and adapter.
Related Modules: app.parsing.resume and shared.resume_contract.
Declaration Index:
- model_output: Build a fixed two-job fixture independently of heuristic source blocks.
- test_missing_detail_rejected: Reject individual missing titles, dates and responsibilities.
- test_missing_detail_rejected.llm: Return one deliberately incomplete extraction.
- test_raw_experience_retained: Preserve every original detail despite concise model claims.
- test_raw_experience_retained.llm: Supply complete coordinates and abbreviated descriptions.
- test_overlapping_or_unordered_lines_rejected: Reject overlapping and disordered source ownership.
- test_overlapping_or_unordered_lines_rejected.llm: Apply an explicit invalid assignment.
- test_duplicate_entries_preserve_all_facts: Merge identical sources without losing fact lists.
- test_duplicate_entries_preserve_all_facts.llm: Supply complementary facts on the same source.
- test_known_experience_cannot_hide_in_other: Catch omitted jobs despite incorrect sections.
- test_known_experience_cannot_hide_in_other.llm: Misclassify all lines as other deliberately.
- test_nonexperience_headings_end_layout_hints: Keep skills, publications and awards out of jobs.
- test_section_notes_preserved_without_inventing_jobs: Retain no-employment notes in Agent context.
- test_section_notes_preserved_without_inventing_jobs.llm: Supply notes and optional project.
- test_invalid_note_ownership_rejected: Reject invalid, duplicate and overlapping note coordinates.
- test_invalid_note_ownership_rejected.llm: Supply a deliberate note ownership error.
- test_authored_mixed_section_keeps_nonexperience_content: Preserve user-placed skills and headings.
- test_authored_mixed_section_keeps_nonexperience_content.llm: Model semantic sections explicitly.
Variable Index:
- SOURCE: Two same-employer jobs with dates, bullets and wrapped responsibilities.
Constraints:
Offline tests validate retention and failure boundaries; they do not prove live semantic accuracy.
"""

import pytest

from app.parsing.resume import (
    document_profile,
    experience_blocks,
    parse_resume_document,
    parse_resume_profile,
)
from shared.resume_contract import UNIT_LABELS
from tests.app.resume_fixture import resume_output

SOURCE = """Experience
SameCo — Intern
2023-01 to 2023-06
  Built an API and
  measured 50 ms.
SameCo — Engineer
2024-01 to 2024-06
• Delivered a reporting system.
Skills
Python
"""


def model_output(data):
    """Inputs: Production-style model request. Outputs: Explicit canonical two-job fixture.
    Logic: Fix section and job coordinates independently of production layout hints.
    Constraints: Synthetic provider output only; no inference or persistent writes.
    """
    return resume_output(
        data,
        section_lines={"experience": list(range(1, 9)), "skills": [9, 10]},
        heading_lines=[1, 9],
        projects=[
            {"name": "SameCo", "source_lines": [2, 3, 4, 5]},
            {"name": "SameCo", "source_lines": [6, 7, 8]},
        ],
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", [2, 3, 4, 5, 7, 8])
async def test_missing_detail_rejected(missing):
    """Inputs: One omitted title/date/responsibility line. Outputs: Coverage error and one call.
    Logic: Keep complete section assignments while omitting one experience coordinate.
    Constraints: Missing details cannot become partial success or trigger an implicit repair call.
    """
    calls = []

    def llm(prompt, data, schema):
        """Inputs: Production request and schema. Outputs: Deliberately incomplete document.
        Logic: Remove only the chosen line. Constraints: Offline model boundary, real validation.
        """
        calls.append(data)
        document = model_output(data)
        for project in document.projects:
            project.source_lines = [n for n in project.source_lines if n != missing]
        return document

    with pytest.raises(ValueError, match="resume_experience_coverage_incomplete"):
        await parse_resume_profile(SOURCE, llm=llm)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_raw_experience_retained():
    """Inputs: Same-name jobs, complete coordinates and deliberately short model summaries.
    Outputs: Distinct IDs and exact original excerpts in existing CandidateProject descriptions.
    Logic: Compare against independently sliced raw lines, including whitespace and wrapped text.
    Constraints: Claims keep their original free-form contract; no scoring changes.
    """

    def llm(prompt, data, schema):
        """Inputs: Request data. Outputs: Complete source assignments with abbreviated claims.
        Logic: Paraphrase names to expose title-filter regressions. Constraints: One offline call.
        """
        document = model_output(data)
        for project in document.projects:
            project.name = "Paraphrased same employer"
            project.description = "Short summary."
            project.claims = ["Implemented software."]
        return document

    profile, _ = await parse_resume_profile(SOURCE, llm=llm)
    assert len(profile.projects) == 2
    assert len({p.project_id for p in profile.projects}) == 2
    for project, numbers in zip(profile.projects, ([2, 3, 4, 5], [6, 7, 8]), strict=True):
        original = "".join(SOURCE.splitlines(keepends=True)[n - 1] for n in numbers)
        assert project.description == "Short summary.\n\nOriginal resume excerpt:\n" + original
        assert project.claims[0].text == "Implemented software."
        assert project.claims[0].source_refs == project.source_refs
    assert set(profile.source_coverage.values()) == {"mapped"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("numbers", "error"),
    [
        ([2, 3, 4, 5, 6], "resume_project_source_overlap"),
        ([5, 4, 3, 2], "resume_project_source_invalid"),
    ],
)
async def test_overlapping_or_unordered_lines_rejected(numbers, error):
    """Inputs: Invalid ownership coordinates and expected error. Outputs: Explicit failure.
    Logic: Keep remaining document complete. Constraints: No silent deduplication of invalid lines.
    """

    def llm(prompt, data, schema):
        """Inputs: Model request. Outputs: Mutated coordinates.
        Logic: Replace first job's ownership only. Constraints: No external service.
        """
        document = model_output(data)
        document.projects[0].source_lines = numbers
        return document

    with pytest.raises(ValueError, match=error):
        await parse_resume_profile(SOURCE, llm=llm)


@pytest.mark.asyncio
async def test_duplicate_entries_preserve_all_facts():
    """Inputs: Duplicate exact source groups with complementary details. Outputs: Two experiences.
    Logic: Assert all descriptions, domains, technologies, claims and metrics survive merging.
    Constraints: Only identical physical ownership permits merging; same names alone do not.
    """

    def llm(prompt, data, schema):
        """Inputs: Request. Outputs: Two jobs plus duplicate first-job facts.
        Logic: Add distinct fields to the repeated source. Constraints: Offline canonical response.
        """
        document = model_output(data)
        first = document.projects[0]
        first.description, first.domain = "First", "Search"
        first.technologies, first.claims, first.metrics = ["Python"], ["Built API"], ["50 ms"]
        duplicate = first.model_copy(deep=True)
        duplicate.description, duplicate.domain = "Second", "Data"
        duplicate.technologies, duplicate.claims, duplicate.metrics = (
            ["SQL"],
            ["Tested API"],
            ["10 tests"],
        )
        document.projects.append(duplicate)
        return document

    profile, _ = await parse_resume_profile(SOURCE, llm=llm)
    assert len(profile.projects) == 2
    first = profile.projects[0]
    assert first.description.startswith("First\nSecond\n\n")
    assert first.domain == "Search\nData"
    assert first.technologies == ["Python", "SQL"]
    assert [c.text for c in first.claims] == ["Built API", "Tested API"]
    assert first.metrics == ["50 ms", "10 tests"]


@pytest.mark.asyncio
async def test_known_experience_cannot_hide_in_other():
    """Inputs: Recognizable jobs incorrectly assigned to other. Outputs: Missing-coverage error.
    Logic: Independent layout hints require these lines even if the section model misclassifies.
    Constraints: This guards known headings, not every possible semantic classification.
    """

    def llm(prompt, data, schema):
        """Inputs: Model request. Outputs: Full sections with zero experience items.
        Logic: Misclassify all lines deliberately. Constraints: One synthetic response.
        """
        return resume_output(data, section_lines={"other": list(range(1, 11))}, projects=[])

    with pytest.raises(ValueError, match="resume_experience_coverage_incomplete"):
        await parse_resume_profile(SOURCE, llm=llm)


@pytest.mark.parametrize(
    "heading",
    [
        "CORE SKILLS",
        "HONORS & AWARDS",
        "OPEN-SOURCE CONTRIBUTIONS & PUBLICATION",
    ],
)
def test_nonexperience_headings_end_layout_hints(heading):
    """Inputs: Common non-experience heading after a job. Outputs: Only actual job source lines.
    Logic: Reproduce the live layout boundary that previously swallowed unrelated sections.
    Constraints: Section content still belongs to canonical model assignment, not heuristic facts.
    """
    blocks = experience_blocks("Experience\nCompany\nBuilt API\n" + heading + "\nUnrelated")
    assert len(blocks) == 1
    assert blocks[0]["line_numbers"] == [2, 3]


@pytest.mark.asyncio
@pytest.mark.parametrize("has_project", [False, True])
async def test_section_notes_preserved_without_inventing_jobs(has_project):
    """Inputs: Explicit zero-employment note with skills and optionally one real academic project.
    Outputs: Exactly one existing-contract topic with retained note, never a fabricated employer.
    Logic: Reuse the established skills-only topic or bind the real project; preserve raw notes.
    Constraints: Notes are context, not claims of work or assessment evidence.
    """
    source = "Experience\nProfessional employment: 0 months.\n" + (
        "Projects\nBuilt a Python API." if has_project else "Skills\nPython"
    )

    def llm(prompt, data, schema):
        """Inputs: Model request. Outputs: Explicit note and optional academic project.
        Logic: Bind note line 2 and project or skill line 4 according to the fixture parameter.
        Constraints: Offline fixture; no fabricated employer or work-experience claim.
        """
        return resume_output(
            data,
            skills=["Python"],
            experience_context_lines=[2],
            heading_lines=[1, 3],
            section_lines={"experience": [1, 2], "projects" if has_project else "skills": [3, 4]},
            projects=[{"name": "Academic API", "source_lines": [4]}] if has_project else [],
        )

    profile, _ = await parse_resume_profile(source, llm=llm)
    assert len(profile.projects) == 1
    assert "Professional employment: 0 months." in profile.projects[0].description
    assert profile.projects[0].name == (
        "Academic API" if has_project else "General technical experience"
    )
    assert all("employment" not in claim.text for claim in profile.projects[0].claims)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("notes", "error"),
    [
        ([99], "resume_context_source_invalid"),
        ([1, 1], "resume_context_source_invalid"),
        ([2], "resume_project_source_overlap"),
    ],
)
async def test_invalid_note_ownership_rejected(notes, error):
    """Inputs: Invalid shared-note coordinates. Outputs: Explicit validation error.
    Logic: Mutate only note ownership in the otherwise valid two-job fixture.
    Constraints: Notes cannot bypass range, uniqueness or project ownership checks.
    """

    def llm(prompt, data, schema):
        """Inputs: Production request. Outputs: One mutated canonical response.
        Logic: Add invalid notes. Constraints: Keep production validation active.
        """
        document = model_output(data)
        document.experience_context_lines = notes
        return document

    with pytest.raises(ValueError, match=error):
        await parse_resume_profile(SOURCE, llm=llm)


@pytest.mark.asyncio
async def test_authored_mixed_section_keeps_nonexperience_content():
    """Inputs: User places skills and a nested heading inside the Projects editor section.
    Outputs: Unchanged authored placement, one real project and retained non-experience context.
    Logic: Override model semantic sections with authoritative rendered intervals while requiring
    explicit ownership for all embedded content. Constraints: No invented jobs or silent repair.
    """
    source = "Projects\nAPI\nBuilt a service.\nSkills\nPython"
    units = {key: "" for key in UNIT_LABELS}
    units["projects"] = source.split("\n", 1)[1]

    def llm(prompt, data, schema):
        """Inputs: Production request with authoritative source coordinates.
        Outputs: Complete project/context attribution and a different semantic section map.
        Logic: Verify the model sees generated heading positions. Constraints: One offline call.
        """
        assert data["authored_heading_lines"] == [1]
        return resume_output(
            data,
            section_lines={"projects": [1, 2, 3], "skills": [4, 5]},
            heading_lines=[1, 4],
            experience_context_lines=[4, 5],
            projects=[{"name": "API", "source_lines": [2, 3]}],
        )

    document = await parse_resume_document(source, llm=llm, units=units)
    assert document.section_lines.projects == [1, 2, 3, 4, 5]
    assert document.heading_lines == [1]
    profile, _ = document_profile(document, source, candidate_id="owned")
    assert len(profile.projects) == 1
    assert "Skills\nPython" in profile.projects[0].description
    assert "API\nBuilt a service.\n" in profile.projects[0].description
