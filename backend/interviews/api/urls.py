"""REST 路由注册。资源使用DefaultRouter，PDF阶段流与实验推荐由独立视图处理。

目录：
（无本地函数或类定义。）

关键变量：
- router：
  注册 questions、sessions 与只读 agent-interviews 资源的 DRF DefaultRouter。
- urlpatterns：
  Django路由列表，包含PDF解析和双向推荐入口；资源路由附加在末尾。
"""

from django.urls import path
from rest_framework.routers import DefaultRouter

from ..recommendation.api import recommend_candidates, recommend_jobs
from ..resume_api import parse_resume_pdf
from .agent_history import AgentHistoryViewSet
from .views import QuestionViewSet, SessionViewSet

router = DefaultRouter()
router.register("questions", QuestionViewSet)
router.register("sessions", SessionViewSet)
router.register("agent-interviews", AgentHistoryViewSet, basename="agent-interview")
urlpatterns = [
    path("resume/parse/", parse_resume_pdf),
    path("recommendations/jobs/", recommend_jobs, name="recommend-jobs"),
    path("recommendations/candidates/", recommend_candidates, name="recommend-candidates"),
] + router.urls
