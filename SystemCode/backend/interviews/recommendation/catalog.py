"""Responsibilities: read explicitly configured job catalog, provide display information and
original model requirements for personal resume recommendations.
Implementation: strictly validate JSON, unique ID, and existing 100-job HTTP limit; do not create
jobs, do not convert training units.
Related Modules: schemas.JobInput defines the feature contract; resume_versions.recommendations
calls the ranking model.
Declaration Index:
- CatalogUnavailable: diagnostic exception for missing or invalid catalog.
- CatalogJob: job card metadata and independent model requirements.
- JobCatalog: bounded job collection with source identifier.
- JobCatalog.unique_jobs: reject duplicate job IDs.
- load_catalog: read-only config file, fail without switching to other sources.
Variable Index:
- logger: Logs configuration status, error type, and count, not catalog content or resumes.

State and Constraints:
CatalogJob.title/company/location/description hold display text; requirements hold model
requirements.
JobCatalog.source_name/source_kind identify the source; jobs contains the complete unsorted
collection without automatic modification or harvesting.
experience indicates experience data; the live catalog is assumed to have been validated by an
administrator and is not scraped in real time.
"""

import logging
from pathlib import Path
from typing import Annotated, Literal

from django.conf import settings
from pydantic import Field, ValidationError, model_validator

from .schemas import MAX_ITEMS, JobInput, Profile, Text

logger = logging.getLogger(__name__)


class CatalogUnavailable(Exception):
    """Function: carry stable error code; logic: thrown from loading boundary; constraint: no
    sensitive paths or raw data included.
    """


class CatalogJob(Profile):
    """Function: declare job card and sorting requirements; input strict JSON, output exactly typed
    fields.
    Logic: display fields do not participate in scoring; constraint: do not infer requirements from
    job description, no database or network side effects.
    """

    title: Text
    company: Text | None = None
    location: Text | None = None
    description: Annotated[str, Field(max_length=2000)] = ""
    requirements: JobInput


class JobCatalog(Profile):
    """Function: identify job source; input name, type, and 1 to 100 jobs, output verified catalog.
    Logic: adhere to existing HTTP candidate pool size; constraint: exceed limit raises error, no
    silent truncation or sampling.
    """

    source_name: Text
    source_kind: Literal["experience", "live"]
    jobs: Annotated[list[CatalogJob], Field(min_length=1, max_length=MAX_ITEMS)]

    @model_validator(mode="after")
    def unique_jobs(self):
        """Input validation instance; returns self. Raises ValueError for duplicate IDs to prevent
        sorting results from being associated with incorrect positions.
        """
        if len({job.requirements.job_id for job in self.jobs}) != len(self.jobs):
            raise ValueError("Duplicate job IDs")
        return self


def load_catalog():
    """Input settings.RECOMMENDATION_JOB_CATALOG; returns a strict JobCatalog without caching or
    rewriting files.
    Empty configuration, read failure, or invalid directory raises a stable error code; logs only
    the exception type, with no retry, rollback, or automatic network connection.
    """
    configured = settings.RECOMMENDATION_JOB_CATALOG
    if not configured:
        logger.info("Job catalog unavailable reason=not_configured")
        raise CatalogUnavailable("job_catalog_not_configured")
    try:
        return JobCatalog.model_validate_json(Path(configured).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValidationError) as exc:
        logger.error("Job catalog unavailable exception=%s", type(exc).__name__)
        raise CatalogUnavailable("job_catalog_invalid") from exc
