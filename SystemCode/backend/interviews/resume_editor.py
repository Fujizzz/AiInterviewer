"""Responsibilities: Validate authored editions and project canonical recommendation facts.
Implementation: Accept explicit user section/field changes; consume the persisted structured
resume for recommendation values. No heading classifier or keyword extractor is used.
Related Modules: shared.resume_contract owns IDs/types; resume_profile owns canonical documents;
resume_versions creates immutable authored snapshots.
Declaration Index:
- ResumeEditionInput: Validate bounded section edits and confirmed shared fact fields.
- ResumeEditionInput.validate: Reject unknown, invalid or empty edition input without writes.
- render_units: Render authored section text in stable order.
- candidate_payload: Project the persisted canonical facts into CandidateInput.
Variable Index:
None
Constraints:
User edits override model values in the next canonical version. Original source and old editions
remain intact; unsupported canonical data causes an explicit error, never independent extraction.
"""

from pydantic import ValidationError as SchemaError
from rest_framework import serializers

from shared.resume_contract import SLOT_UNITS, UNIT_LABELS, ResumeFacts

from .recommendation.schemas import CandidateInput


class ResumeEditionInput(serializers.Serializer):
    """Functionality: Validate section edits and strict recommendation slots for a new snapshot.
    Logic: label bounds the optional display name; units preserves supplied section whitespace;
    slots contains optional confirmed values. validate applies aggregate and schema constraints.
    Constraints: This serializer performs no writes; the API creates the new edition after success.
    """

    label = serializers.CharField(max_length=120, required=False, default="", allow_blank=True)
    units = serializers.DictField(
        child=serializers.CharField(max_length=200000, allow_blank=True, trim_whitespace=False)
    )
    slots = serializers.JSONField(required=False, default=dict)

    def validate(self, attrs):
        """Functionality: Check an edition's aggregate body and confirmed recommendation fields.
        Inputs: Deserialized attrs and self.initial_data for detecting unknown request keys.
        Outputs: attrs with all section IDs present; invalid requests raise ValidationError.
        Logic: Reject unknown keys, empty/oversized rendered text and invalid CandidateInput values.
        Constraints: Does not infer slots or write records; schema errors report field names only.
        """
        if set(self.initial_data) - {"label", "units", "slots"}:
            raise serializers.ValidationError("Unknown edition fields.")
        if set(attrs["units"]) - set(UNIT_LABELS):
            raise serializers.ValidationError("Unknown resume units.")
        units = {key: attrs["units"].get(key, "") for key in UNIT_LABELS}
        text = render_units(units)
        if not text.strip() or len(text) > 200000:
            raise serializers.ValidationError(
                "Resume text must be nonempty and at most 200000 characters."
            )
        slots = attrs["slots"]
        if not isinstance(slots, dict) or set(slots) - set(SLOT_UNITS):
            raise serializers.ValidationError("Unknown recommendation slots.")
        try:
            CandidateInput.model_validate({"candidate_id": "validation-only", **slots})
        except SchemaError as exc:
            fields = [".".join(str(part) for part in error["loc"]) for error in exc.errors()]
            raise serializers.ValidationError({"slots": fields}) from exc
        attrs.update(units=units)
        return attrs


def render_units(units):
    """Functionality: Render validated editor sections as standalone resume text.
    Inputs: A complete mapping of UNIT_LABELS keys to strings.
    Outputs: Nonempty sections joined with their canonical headings and blank-line separators.
    Logic: Follow the stable section order and omit whitespace-only sections.
    Constraints: Preserve characters within nonempty sections; no inference, logging or writes.
    """
    return "\n\n".join(
        f"{label}\n{units[key]}" for key, label in UNIT_LABELS.items() if units[key].strip()
    )


def candidate_payload(version):
    """Functionality: Build recommendation input from the shared persisted document.
    Inputs: Owned resume version with a valid canonical cache.
    Outputs: CandidateInput dictionary using the version UUID and the exact shared fact values.
    Logic: Read and validate the canonical document, then project its eleven typed fields.
    Constraints: No model call or rule extraction; stale/missing caches raise ValueError.
    """
    from .resume_profile import read_document

    document = read_document(version)
    return CandidateInput.model_validate(
        {
            "candidate_id": str(version.pk),
            **document.model_dump(include=set(ResumeFacts.model_fields)),
        }
    ).model_dump()
