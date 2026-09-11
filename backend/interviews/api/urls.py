"""REST 路由注册。DefaultRouter 生成题库与场次资源路径，动作由视图声明。

目录：
- 配置常量、路由声明或子模块说明（无运行时函数）。
"""

from rest_framework.routers import DefaultRouter

from .views import QuestionViewSet, SessionViewSet

router = DefaultRouter()
router.register("questions", QuestionViewSet)
router.register("sessions", SessionViewSet)
urlpatterns = router.urls
