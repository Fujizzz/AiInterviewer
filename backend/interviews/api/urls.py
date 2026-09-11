"""REST 路由注册。DefaultRouter 生成题库与场次资源路径，动作由视图声明。

目录：
（无本地函数或类定义。）

关键变量：
- router：
  注册 questions 与 sessions 资源的 DRF DefaultRouter。
- urlpatterns：
  由 Django 使用的路由列表；具体路径及模块职责见下方装配语句。
"""

from rest_framework.routers import DefaultRouter

from .views import QuestionViewSet, SessionViewSet

router = DefaultRouter()
router.register("questions", QuestionViewSet)
router.register("sessions", SessionViewSet)
urlpatterns = router.urls
