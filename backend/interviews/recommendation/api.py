"""Responsibilities: Map two recommendation requests to the fixed ranking service without persisting
request details.
Implementation: Reuse the local access policy and unified DRF error responses for candidate-to-job
and job-to-candidate sorting.
Related Modules: schemas validates requests; runtime loads the model and ranks pairs;
resume_versions may call the personal recommendation flow.

Declaration Index:
- validate_request: convert Pydantic validation errors into HTTP 400 without original input.
- respond: execute sorting and map feature errors/model failures to 400/503.
- recommend_jobs: POST endpoint from candidates to job list.
- recommend_candidates: POST endpoint from jobs to candidate list.

Variable Index:
None

Design Notes:
Decorator only registers HTTP method; input contract in schemas, model loading in runtime; no LLM
calls or database writes.
"""

from pydantic import ValidationError as SchemaError
from rest_framework.decorators import api_view
from rest_framework.exceptions import APIException, ValidationError
from rest_framework.response import Response

from .runtime import ModelUnavailable, rank_pairs
from .schemas import CandidatesRequest, JobsRequest


def validate_request(schema, data):
    """Function: validate request object; input schema class and parsed JSON, output validated
    object.

    Logic: errors return only field path and error type, not input value or context; constraint: do
    not swallow non-validation exceptions.
    """
    try:
        return schema.model_validate(data)
    except SchemaError as exc:
        errors = [
            {"field": ".".join(map(str, error["loc"])), "code": error["type"]}
            for error in exc.errors(include_input=False, include_context=False)
        ]
        raise ValidationError({"fields": errors}) from exc


def respond(pairs, direction):
    """Function: construct sort response; input validated pair/direction, output 200 Response.

    Logic: feature range error → 400, model unavailable → 503; constraint: no retry, no fallback,
    error body does not contain resume.
    """
    try:
        return Response(rank_pairs(pairs, direction))
    except ValueError as exc:
        raise ValidationError("Feature values are outside the supported range") from exc
    except ModelUnavailable as exc:
        error = APIException("Recommendation model unavailable; inspect server logs")
        error.status_code = 503
        error.default_code = "recommendation_unavailable"
        raise error from exc


@api_view(["POST"])
def recommend_jobs(request):
    """Function: rank jobs by preference score; input one person and list of jobs, output two scores
    and missing data information; no persistence.
    """
    data = validate_request(JobsRequest, request.data)
    return respond([(data.candidate, job) for job in data.jobs], "jobs")


@api_view(["POST"])
def recommend_candidates(request):
    """Function: rank candidates by synthesized winner score from job side; input one job and list
    of candidates, output experimental sort result.
    """
    data = validate_request(CandidatesRequest, request.data)
    return respond([(candidate, data.job) for candidate in data.candidates], "candidates")
