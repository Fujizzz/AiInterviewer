"""职责：提供简单注册、会话登录、退出和 HTTP 登录门禁。

实现：复用 Django 用户、密码哈希和数据库 session；表单经 CSRF 校验，重定向只允许本站。
关联：account.html、config.urls、SessionMiddleware；生产启用门禁，本地开发保留回环模式。

目录：
- safe_next：筛选本站跳转目标，拒绝外站或账号页循环。
- account_page：处理注册/登录，成功后轮换 session 并跳转，失败展示固定错误文案。
- sign_out：仅以带 CSRF 的 POST 注销当前会话。
- AccountRequiredMiddleware：按部署设置保护页面和 API。
- AccountRequiredMiddleware.__init__：保存下游处理器。
- AccountRequiredMiddleware.__call__：放行公开入口，匿名 API 返回 401，匿名页面跳转登录。

关键变量：
- logger：记录操作类别和用户 ID，不记录用户名、密码或 session token。
- PUBLIC_PATHS：无需登录的主页、账号页及退出入口。
- PUBLIC_ASSETS：账号页所需的公开样式和语言资源精确路径。

约束：
用户名非空且最多 150 字符；密码非空且最多 128 字符，无复杂度、邮箱或确认密码验证。
此处没有重试或匿名降级；数据库异常保持失败。CSRF 是请求保护，不增加用户填写项。
"""

import logging
from urllib.parse import urlencode, urlsplit

from django.conf import settings
from django.contrib.auth import authenticate, get_user_model, login, logout
from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_http_methods, require_POST

logger = logging.getLogger(__name__)
PUBLIC_PATHS = {"/", "/login/", "/register/", "/logout/"}
PUBLIC_ASSETS = {
    "/stream-demo/style.css",
    "/stream-demo/home.css",
    "/stream-demo/account.css",
    "/stream-demo/i18n.js",
}


def safe_next(request):
    """读取 GET/POST 的 next；仅返回本站 HTTP(S) 目标，缺失或非法时返回主页，无 I/O。"""
    target = request.POST.get("next", request.GET.get("next", "/"))
    if not url_has_allowed_host_and_scheme(
        target, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ) or urlsplit(target).path in {"/login/", "/register/", "/logout/"}:
        return "/"
    return target


@never_cache
@csrf_protect
@require_http_methods(["GET", "POST"])
def account_page(request, mode):
    """输入请求与路由固定的 login/register 模式，返回表单或成功跳转。

    注册原子写用户，唯一冲突仅在确认用户名已存在时映射为表单错误；其他异常原样传播。
    登录使用 Django 后端验证，不调用密码复杂度验证器；绝不回填或记录密码。
    成功登录轮换 session 和 CSRF token；登录中的用户直接进入安全的 next 目标。
    """
    target = safe_next(request)
    if request.user.is_authenticated:
        return redirect(target)
    username = ""
    error = ""
    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "")
        if not username or len(username) > 150 or not password or len(password) > 128:
            error = "auth_error_fields"
        elif mode == "register":
            users = get_user_model().objects
            try:
                with transaction.atomic():
                    user = users.create_user(username=username, password=password)
            except IntegrityError:
                if not users.filter(username=username).exists():
                    raise
                error = "auth_error_duplicate"
            else:
                login(request, user, backend="django.contrib.auth.backends.ModelBackend")
                logger.info("Account registered user_id=%s", user.pk)
                return redirect(target)
        else:
            user = authenticate(request, username=username, password=password)
            if user is None:
                error = "auth_error_credentials"
            else:
                login(request, user)
                logger.info("Account logged in user_id=%s", user.pk)
                return redirect(target)
        logger.warning("Account form rejected action=%s reason=%s", mode, error)
    return render(
        request,
        "account.html",
        {
            "mode": mode,
            "next": target,
            "username": username,
            "error_key": error,
        },
        status=400 if error else 200,
    )


@never_cache
@csrf_protect
@require_POST
def sign_out(request):
    """以 POST 和有效 CSRF token 清除当前数据库 session，返回主页；不删除账号或面试记录。"""
    user_id = request.user.pk
    logout(request)
    logger.info("Account logged out user_id=%s", user_id)
    return redirect("/")


class AccountRequiredMiddleware:
    """功能：生产 HTTP 登录门禁；只允许公开路径匿名访问，不依赖 UUID 隐蔽性。"""

    def __init__(self, get_response):
        """保存下游处理器；实例无数据库或网络副作用。"""
        self.get_response = get_response

    def __call__(self, request):
        """读取部署开关和 SessionMiddleware/AuthenticationMiddleware 设置的用户身份。

        匿名 API 明确返回 401 JSON，页面跳转登录并保留本站路径；认证失败不进入业务层。
        默认本地回环开发模式不强制登录，数据查询仍按用户或未归属数据过滤。
        """
        if (
            not settings.INTERVIEW_REQUIRE_LOGIN
            or request.user.is_authenticated
            or request.path in PUBLIC_PATHS
            or request.path in PUBLIC_ASSETS
        ):
            return self.get_response(request)
        if request.path.startswith("/api/"):
            response = JsonResponse(
                {"error": {"code": "authentication_required", "detail": "Please sign in."}},
                status=401,
            )
            response["Cache-Control"] = "no-store"
            return response
        return redirect("/login/?" + urlencode({"next": request.get_full_path()}))
