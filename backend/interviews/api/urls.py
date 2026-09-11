"""REST 路由注册。DefaultRouter 生成题库与场次资源路径，动作由视图声明。

目录：
- router：注册 questions 与 sessions 资源；urlpatterns：导出生成的路径。
"""

from rest_framework.routers import DefaultRouter

from .views import QuestionViewSet, SessionViewSet

router = DefaultRouter()
router.register("questions", QuestionViewSet)
router.register("sessions", SessionViewSet)
urlpatterns = router.urls
