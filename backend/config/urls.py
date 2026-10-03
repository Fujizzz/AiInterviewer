"""根路由表。组合测试资源、健康检查与 REST 路由，不直接实现业务动作。

目录：
（无本地函数或类定义。）

关键变量：
- urlpatterns：
  由 Django 使用的路由列表；具体路径及模块职责见下方装配语句。

设计说明：
路由说明：/ 提供统一主页；/stream-demo/ 提供传输诊断页及其子路径资源；
/agent/ 提供需登录的版本语音面试页，/resumes/ 提供姓名/邮箱及简历维护的个人中心。
/interview-review/ 提供独立的本人面试复盘列表，顶部导航与个人中心并列。
各页面通过普通链接导航，不共享面试状态。
/api/health/ 检查数据库连接，/api/speech/ 提供语音与临时音频，/api/ 注册业务接口；
login/register/logout 提供账号操作。
"""

from django.urls import include, path
from interviews.accounts import account_page, sign_out
from interviews.api.views import health
from interviews.demo import demo_asset
from interviews.speech.views import audio, tts

urlpatterns = [
    path("login/", account_page, {"mode": "login"}),
    path("register/", account_page, {"mode": "register"}),
    path("logout/", sign_out),
    path("", demo_asset, {"name": "home.html"}),
    path("agent/", demo_asset, {"name": "agent.html"}),
    path("resumes/", demo_asset, {"name": "resumes.html"}),
    path("interview-review/", demo_asset, {"name": "interview-review.html"}),
    path("stream-demo/", demo_asset, {"name": "index.html"}),
    path("stream-demo/<str:name>", demo_asset),
    path("api/health/", health),
    path("api/speech/tts/", tts),
    path("api/speech/audio/<uuid:utterance_id>/", audio),
    path("api/", include("interviews.api.urls")),
]
