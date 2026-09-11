"""REST 错误响应规范。将预期异常映射为稳定 JSON，将系统异常记录到控制台。

目录：
- Conflict：
  语义为状态冲突的 API 异常，固定 HTTP 409 与 conflict 错误码。
- api_exception_handler：
  功能：统一 DRF 异常的 JSON 外层结构。

关键变量：
- logger：
  当前模块的控制台日志入口；上下文标识及异常处理方式见相应函数。
"""

import logging

from django.db import OperationalError
from rest_framework.exceptions import APIException
from rest_framework.response import Response
from rest_framework.views import exception_handler

logger = logging.getLogger(__name__)


class Conflict(APIException):
    """语义为状态冲突的 API 异常，固定 HTTP 409 与 conflict 错误码。"""

    status_code = 409
    default_code = "conflict"
    default_detail = "The resource state does not allow this operation."


def api_exception_handler(exc, context):
    """功能：统一 DRF 异常的 JSON 外层结构。
    方法：数据库操作错误返回 503；框架可识别异常保留其状态；其余错误返回 500。
    输入：异常与 DRF 上下文；返回：Response。
    副作用：预期错误记录路径和类型；系统错误通过 logger.exception 附带异常文本与堆栈。
    本层不主动序列化请求正文，但不对上游异常文本执行脱敏；调用者不应在异常中嵌入秘密。
    不执行重试；HTTP 500/503 响应只返回固定说明，不附带服务端异常文本。"""
    request = context.get("request")
    path = request.path if request else "unknown"
    if isinstance(exc, OperationalError):
        logger.exception(
            "Database operation failed path=%s; "
            "inspect database path, migrations and concurrent writers",
            path,
        )
        return Response(
            {
                "error": {
                    "code": "database_unavailable",
                    "detail": (
                        "Database unavailable. Inspect server logs; "
                        "no automatic retry was performed."
                    ),
                }
            },
            status=503,
        )
    response = exception_handler(exc, context)
    if response is None:
        logger.exception("Unhandled API failure path=%s", path)
        return Response(
            {"error": {"code": "internal_error", "detail": "Request failed; inspect server logs."}},
            status=500,
        )
    logger.warning(
        "API rejected path=%s status=%s exception=%s",
        path,
        response.status_code,
        type(exc).__name__,
    )
    response.data = {
        "error": {"code": getattr(exc, "default_code", "request_error"), "detail": response.data}
    }
    return response
