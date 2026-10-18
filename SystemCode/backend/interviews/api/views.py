"""Responsibilities: Adapt REST requests and responses for practice interview resources.
Implementation: Validate input, invoke business services, and serialize JSON without embedding
transaction logic.
Related Modules: api.serializers defines the data contract; services owns transactional decisions;
api.urls registers these views.

Declaration Index:
- health:
  Function: Execute SELECT 1 to check current database connection availability.
- QuestionViewSet:
  Provides question bank query, creation, and partial update; no delete interface exposed;
  deactivation controlled by enabled flag.
- SessionViewSet:
  Session REST adapter, filtered by creating user; write actions delegated to transaction service.
- SessionViewSet.get_queryset: Only return sessions owned by current user; all details/updates reuse
  this ownership check.
- SessionViewSet.create:
  Validate creation parameters, call create_session, return HTTP 201 and full session snapshot.
- SessionViewSet.finish:
  Validate request version, call finish_session, return updated status and version.
- SessionViewSet.item:
  Validate single-question action, call transaction service by session/question ID in URL, return
  full session.

Variable Index:
None
"""

from django.db import connection
from rest_framework import mixins, viewsets
from rest_framework.decorators import action, api_view
from rest_framework.response import Response

from .. import services
from ..models import PracticeSession, Question
from .serializers import (
    CreateSessionSerializer,
    ItemCommandSerializer,
    QuestionSerializer,
    SessionSerializer,
    VersionSerializer,
)


@api_view(["GET"])
def health(request):
    """Function: Execute SELECT 1 to check current database connection availability.
    Return: JSON containing status and database engine name; connection exception mapped by unified
    error handler.
    """
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
    return Response({"status": "ok", "database": connection.vendor})


class QuestionViewSet(viewsets.ModelViewSet):
    """Provides question bank query, creation, and partial update; no delete interface exposed;
    deactivation controlled by enabled flag.
    """

    queryset = Question.objects.all()
    serializer_class = QuestionSerializer
    http_method_names = ["get", "post", "patch", "head", "options"]


class SessionViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """Isolate sessions by user; local anonymous development only sees old data with owner null;
    cross-user read/write returns 404.
    """

    queryset = PracticeSession.objects.prefetch_related("items")
    serializer_class = SessionSerializer

    def get_queryset(self):
        """Obtain user ID from authenticated request.user and filter queryset; do not accept owner
        specified via query parameters.
        """
        return super().get_queryset().filter(owner_id=getattr(self.request.user, "pk", None))

    def create(self, request):
        """Validate parameters, bind current authenticated user to new session, and return 201;
        clients cannot specify other owners.
        """
        data = CreateSessionSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        session = services.create_session(owner=request.user, **data.validated_data)
        return Response(SessionSerializer(session).data, status=201)

    @action(detail=True, methods=["post"])
    def finish(self, request, pk=None):
        """First verify session ownership, then validate version and call finish_session;
        unauthorized access returns 404 without any write operation.
        """
        self.get_object()
        data = VersionSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        session = services.finish_session(pk, **data.validated_data)
        return Response(SessionSerializer(session).data)

    @action(detail=True, methods=["patch"], url_path=r"items/(?P<item_id>[0-9a-f-]{36})")
    def item(self, request, pk=None, item_id=None):
        """First verify parent session ownership, then call transaction service by session/question
        ID; unauthorized access does not modify status or version.
        """
        self.get_object()
        data = ItemCommandSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        session = services.update_item(pk, item_id, data.validated_data)
        return Response(SessionSerializer(session).data)
