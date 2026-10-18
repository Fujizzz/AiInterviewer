"""Responsibilities: Define the REST request and response contracts.
Implementation: Strictly validate writable fields, preserve requested question order, constrain
action parameters, and expose immutable question snapshots.
Related Modules: api.views invokes the transaction services after serializer validation; api.urls
registers the view adapters.

Declaration Index:
- StrictInputMixin:
  Strict input policy: reject unknown and read-only fields to avoid silently ignoring call errors.
- StrictInputMixin.to_internal_value:
  Function: Validate field whitelist before DRF type conversion.
- QuestionSerializer:
  Question bank read-write contract; primary key and creation/update timestamps managed by server.
- QuestionSerializer.Meta:
  Declare external and read-only fields, maintaining auditability of question bank write scope.
- SessionQuestionSerializer:
  Single-question use only for response display, including question snapshot, answer, and server
  time.
- SessionQuestionSerializer.Meta:
  Explicitly list response fields for single questions, preventing automatic exposure of new
  internal model fields.
- SessionSerializer:
  Session response contract, nesting read-only question lists in model order.
- SessionSerializer.Meta:
  Explicitly expose status, version, fixed duration, and question snapshots.
- CreateSessionSerializer:
  Creation request contract: optional non-empty UUID sequence, up to 100 items.
- CreateSessionSerializer.validate_question_ids:
  Check for duplicate IDs using set length; on success, return list unchanged, preserving
  user-specified order.
- VersionSerializer:
  Request base class requiring version to be an integer ≥ 1 when changing session.
- ItemCommandSerializer:
  Single-question action contract, constraining action enumeration, answer length, and optional
  duration range.
- ItemCommandSerializer.validate:
  Cross-validate action and answer fields: only complete accepts answer and duration; other
  combinations are explicitly rejected.

Variable Index:
None

Key State Explanations:
All Meta.fields explicitly declare public fields; read_only_fields restrict writing.
VersionSerializer.version controls concurrency; ItemCommandSerializer.action constrains state
actions.
answer_text/duration_ms allowed only with complete submission.
"""

from rest_framework import serializers

from ..models import PracticeSession, Question, SessionQuestion


class StrictInputMixin:
    """Strict input policy: reject unknown and read-only fields to avoid silently ignoring call
    errors.
    """

    def to_internal_value(self, data):
        """Function: Validate field whitelist before DRF type conversion.
        Method: Allow sets from non-read-only fields; excess keys form field-level ValidationError.
        Return: DRF conversion result; non-dictionary inputs passed to DRF’s original type
        validation.
        """
        if isinstance(data, dict):
            allowed = {name for name, field in self.fields.items() if not field.read_only}
            unknown = set(data) - allowed
            if unknown:
                raise serializers.ValidationError(
                    {name: "Unknown or read-only field." for name in sorted(unknown)}
                )
        return super().to_internal_value(data)


class QuestionSerializer(StrictInputMixin, serializers.ModelSerializer):
    """Question bank read-write contract; primary key and creation/update timestamps managed by
    server.
    """

    class Meta:
        """Declare external and read-only fields, maintaining auditability of question bank write
        scope.
        """

        model = Question
        fields = ["id", "text", "position", "enabled", "created_at", "updated_at"]
        read_only_fields = ["id", "created_at", "updated_at"]


class SessionQuestionSerializer(serializers.ModelSerializer):
    """Single-question use only for response display, including question snapshot, answer, and
    server time.
    """

    class Meta:
        """Explicitly list response fields for single questions, preventing automatic exposure of
        new internal model fields.
        """

        model = SessionQuestion
        fields = [
            "id",
            "question_id",
            "question_text",
            "position",
            "status",
            "answer_text",
            "duration_ms",
            "started_at",
            "finished_at",
        ]


class SessionSerializer(serializers.ModelSerializer):
    """Session response contract, nesting read-only question lists in model order.
    """

    items = SessionQuestionSerializer(many=True, read_only=True)

    class Meta:
        """Explicitly expose status, version, fixed duration, and question snapshots.
        """

        model = PracticeSession
        fields = [
            "id",
            "status",
            "version",
            "prep_seconds",
            "answer_seconds",
            "started_at",
            "finished_at",
            "items",
        ]


class CreateSessionSerializer(StrictInputMixin, serializers.Serializer):
    """Creation request contract: optional non-empty UUID sequence, up to 100 items.
    """

    question_ids = serializers.ListField(
        child=serializers.UUIDField(), required=False, allow_empty=False, max_length=100
    )

    def validate_question_ids(self, value):
        """Check for duplicate IDs using set length; on success, return list unchanged, preserving
        user-specified order.
        """
        if len(set(value)) != len(value):
            raise serializers.ValidationError("Duplicate question IDs are not allowed.")
        return value


class VersionSerializer(StrictInputMixin, serializers.Serializer):
    """Request base class requiring version to be an integer ≥ 1 when changing session.
    """

    version = serializers.IntegerField(min_value=1)


class ItemCommandSerializer(VersionSerializer):
    """Single-question action contract, constraining action enumeration, answer length, and optional
    duration range.
    """

    action = serializers.ChoiceField(choices=["start", "complete", "skip"])
    answer_text = serializers.CharField(
        required=False, allow_blank=True, max_length=20000, trim_whitespace=False
    )
    duration_ms = serializers.IntegerField(required=False, min_value=0, max_value=2147483647)

    def validate(self, attrs):
        """Cross-validate action and answer fields: only complete accepts answer and duration; other
        combinations are explicitly rejected.
        """
        if attrs["action"] != "complete" and ({"answer_text", "duration_ms"} & attrs.keys()):
            raise serializers.ValidationError("Answer fields are accepted only for complete.")
        return attrs
