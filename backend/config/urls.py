"""根路由表。组合测试资源、健康检查与 REST 路由，不直接实现业务动作。

目录：
- urlpatterns：首页、stream-demo 资源、api/health 健康检查及 api 资源路由。
"""

from django.urls import include, path
from interviews.api.views import health
from interviews.demo import demo_asset

urlpatterns = [
    path("", demo_asset, {"name": "index.html"}),
    path("stream-demo/<str:name>", demo_asset),
    path("api/health/", health),
    path("api/", include("interviews.api.urls")),
]
