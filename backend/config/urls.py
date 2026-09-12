"""根路由表。组合测试资源、健康检查与 REST 路由，不直接实现业务动作。

目录：
（无本地函数或类定义。）

关键变量：
- urlpatterns：
  由 Django 使用的路由列表；具体路径及模块职责见下方装配语句。

设计说明：
路由说明：/ 提供统一主页；/stream-demo/ 提供传输诊断页及其子路径资源；
/agent/ 提供文字面试页。各页面通过普通链接导航，不共享面试状态。
/api/health/ 检查数据库连接，/api/ 注册业务接口。
"""

from django.urls import include, path
from interviews.api.views import health
from interviews.demo import demo_asset

urlpatterns = [
    path("", demo_asset, {"name": "home.html"}),
    path("agent/", demo_asset, {"name": "agent.html"}),
    path("stream-demo/", demo_asset, {"name": "index.html"}),
    path("stream-demo/<str:name>", demo_asset),
    path("api/health/", health),
    path("api/", include("interviews.api.urls")),
]
