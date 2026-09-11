"""HTTP 本机访问中间件。复用 access 策略并在业务处理前拒绝非本机或跨源请求。

目录：
- LocalOnlyMiddleware：
  可调用中间件：本机检查通过后才交给下一处理器，保持原响应不变。
- LocalOnlyMiddleware.__init__：
  保存 Django 下游处理器；构造阶段不执行网络或数据库操作。
- LocalOnlyMiddleware.__call__：
  功能：请求进入业务层前执行地址及 Origin 检查。

关键变量：
（无模块级变量。）
"""

from django.http import JsonResponse

from .access import is_loopback, same_origin


class LocalOnlyMiddleware:
    """可调用中间件：本机检查通过后才交给下一处理器，保持原响应不变。"""

    def __init__(self, get_response):
        """保存 Django 下游处理器；构造阶段不执行网络或数据库操作。"""
        self.get_response = get_response

    def __call__(self, request):
        """功能：请求进入业务层前执行地址及 Origin 检查。
        返回：拒绝时为带 local_only 代码的 403 JSON；通过时原样委派 get_response。"""
        if not is_loopback(request.META.get("REMOTE_ADDR", "")) or not same_origin(
            request.headers.get("Origin"), request.get_host(), request.scheme
        ):
            return JsonResponse(
                {
                    "error": {
                        "code": "local_only",
                        "detail": "Use this service from the same local origin.",
                    }
                },
                status=403,
            )
        return self.get_response(request)
