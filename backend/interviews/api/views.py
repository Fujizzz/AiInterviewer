"""REST 视图适配层。序列化输入、调用业务服务并返回 JSON，不内联事务逻辑。

目录：
- health：
  功能：执行 SELECT 1 检查当前数据库连接可用性。
- QuestionViewSet：
  提供题库查询、新增与局部修改；不开放删除接口，停用由 enabled 控制。
- SessionViewSet：
  场次 REST 适配器，按创建用户过滤，写动作委派事务服务。
- SessionViewSet.get_queryset：仅返回当前用户的场次，所有详情/更新先复用该归属检查。
- SessionViewSet.create：
  校验创建参数，调用 create_session，返回 HTTP 201 和完整场次快照。
- SessionViewSet.finish：
  校验请求版本，调用 finish_session，并返回更新后的状态与版本。
- SessionViewSet.item：
  校验单题动作，按 URL 中场次/单题 ID 调用事务服务，返回完整场次。

关键变量：
（无模块级变量。）
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
    """按用户隔离场次；本地匿名开发仅查看 owner 为空的旧数据，跨用户读写均返回 404。"""

    queryset = PracticeSession.objects.prefetch_related("items")
    serializer_class = SessionSerializer

    def get_queryset(self):
        """从已认证 request.user 取得用户 ID 并过滤查询集；不接受查询参数指定 owner。"""
        return super().get_queryset().filter(owner_id=getattr(self.request.user, "pk", None))

    def create(self, request):
        """校验参数，把当前认证用户绑定到新场次并返回 201；客户端不能指定其他 owner。"""
        data = CreateSessionSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        session = services.create_session(owner=request.user, **data.validated_data)
        return Response(SessionSerializer(session).data, status=201)

    @action(detail=True, methods=["post"])
    def finish(self, request, pk=None):
        """先核查场次归属，再校验版本和调用 finish_session；越权 404 且不产生写入。"""
        self.get_object()
        data = VersionSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        session = services.finish_session(pk, **data.validated_data)
        return Response(SessionSerializer(session).data)

    @action(detail=True, methods=["patch"], url_path=r"items/(?P<item_id>[0-9a-f-]{36})")
    def item(self, request, pk=None, item_id=None):
        """先核查父场次归属，再按场次/单题 ID 调用事务服务；越权不修改状态或版本。"""
        self.get_object()
        data = ItemCommandSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        session = services.update_item(pk, item_id, data.validated_data)
        return Response(SessionSerializer(session).data)
