"""REST 路由注册。资源由 DefaultRouter 生成，PDF 阶段流由独立异步视图处理。

目录：
（无本地函数或类定义。）

关键变量：
- router：
  注册 questions 与 sessions 资源的 DRF DefaultRouter。
- urlpatterns：
  由 Django 使用的路由列表；具体路径及模块职责见下方装配语句。
"""

from django.urls import path
from rest_framework.routers import DefaultRouter

from ..resume_api import parse_resume_pdf
from .views import QuestionViewSet, SessionViewSet

router = DefaultRouter()
router.register("questions", QuestionViewSet)
router.register("sessions", SessionViewSet)
urlpatterns = [path("resume/parse/", parse_resume_pdf)] + router.urls
