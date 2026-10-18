"""Responsibilities: provide personal center's own basic profile read and maintenance, reuse Django
user table, no additional profile models.
Implementation: session authentication, CSRF, and whitelisted fields; PATCH updates only submitted
name/email, account identity and permissions are read-only.
Related Modules: api.urls registers /api/profile/; resumes.html/resumes.js displays and maintains
basic profile.

Declaration Index:
- ProfileSerializer: Serialize personal profile with limited field validation.
- ProfileSerializer.Meta: Define public identity fields and editable name/email fields.
- ProfileSerializer.validate: Reject unknown fields and write attempts to read-only fields.
- profile: Authenticated GET/PATCH, retrieve or update self and return no-cache response.

Variable Index:
- logger: Log only user ID and updated field names, without recording name, email, or session
  information.

Configuration Notes:
ProfileSerializer.Meta.model uses Django User; fields are whitelist of public fields,
read_only_fields
prevent modification of ID/username/registration date, extra_kwargs maintain model field length and
allow name/email to be empty.
Constraints: first_name used as personal center name; email is only contact information, not
verified email or login credential.
"""
import logging

from django.contrib.auth import get_user_model
from rest_framework import serializers
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

logger = logging.getLogger(__name__)


class ProfileSerializer(serializers.ModelSerializer):
    """Function: personal basic profile contract; logic: model length/email format validation;
    constraints: do not expose password or permission fields.
    """

    class Meta:
        """Function: fixed profile fields; logic: identity read-only, name and email nullable;
        constraints: no new model or validation state added.
        """
        model = get_user_model()
        fields = ["id", "username", "first_name", "email", "date_joined"]
        read_only_fields = ["id", "username", "date_joined"]
        extra_kwargs = {
            "first_name": {"required": False, "allow_blank": True},
            "email": {"required": False, "allow_blank": True},
        }

    def validate(self, attrs):
        """Input validated model fields; reject any non-editable fields in input; output unmodified
        dictionary without write operations.
        """
        if set(self.initial_data) - {"first_name", "email"}:
            raise serializers.ValidationError("Only first_name and email can be updated.")
        return attrs


@api_view(["GET", "PATCH"])
@permission_classes([IsAuthenticated])
def profile(request):
    """Input real authenticated request; GET returns personal profile, PATCH validates then updates
    only submitted fields and logs field names.

    No duplicate user ID input; empty PATCH does not write to database. Format errors returned as
    400 by existing exception handler, no partial save.
    Output private no-cache JSON; name stored only in first_name, email does not trigger
    verification email or change authentication method.
    """
    serializer = ProfileSerializer(request.user)
    if request.method == "PATCH":
        serializer = ProfileSerializer(request.user, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        fields = list(serializer.validated_data)
        if fields:
            for name, value in serializer.validated_data.items():
                setattr(request.user, name, value)
            request.user.save(update_fields=fields)
            logger.info("Profile updated owner=%s fields=%s", request.user.pk, sorted(fields))
    response = Response(serializer.data)
    response["Cache-Control"] = "no-store, private"
    response["X-Content-Type-Options"] = "nosniff"
    return response
