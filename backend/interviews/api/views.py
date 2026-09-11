"""REST 视图适配层。序列化输入、调用业务服务并返回 JSON，不内联事务逻辑。

目录：
- health
- QuestionViewSet
- SessionViewSet
- SessionViewSet.create
- SessionViewSet.finish
- SessionViewSet.item
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
    """功能：执行 SELECT 1 检查当前数据库连接可用性。
    返回：包含 status 与数据库引擎名的 JSON；连接异常由统一错误处理器映射。"""
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
    return Response({"status": "ok", "database": connection.vendor})


class QuestionViewSet(viewsets.ModelViewSet):
    """提供题库查询、新增与局部修改；不开放删除接口，停用由 enabled 控制。"""

    queryset = Question.objects.all()
    serializer_class = QuestionSerializer
    http_method_names = ["get", "post", "patch", "head", "options"]


class SessionViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """场次 REST 适配器，列表预取单题，写动作委派事务服务。"""

    queryset = PracticeSession.objects.prefetch_related("items")
    serializer_class = SessionSerializer

    def create(self, request):
        """校验创建参数，调用 create_session，返回 HTTP 201 和完整场次快照。"""
        data = CreateSessionSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        session = services.create_session(**data.validated_data)
        return Response(SessionSerializer(session).data, status=201)

    @action(detail=True, methods=["post"])
    def finish(self, request, pk=None):
        """校验请求版本，调用 finish_session，并返回更新后的状态与版本。"""
        data = VersionSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        session = services.finish_session(pk, **data.validated_data)
        return Response(SessionSerializer(session).data)

    @action(detail=True, methods=["patch"], url_path=r"items/(?P<item_id>[0-9a-f-]{36})")
    def item(self, request, pk=None, item_id=None):
        """校验单题动作，按 URL 中场次/单题 ID 调用事务服务，返回完整场次。"""
        data = ItemCommandSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        session = services.update_item(pk, item_id, data.validated_data)
        return Response(SessionSerializer(session).data)
