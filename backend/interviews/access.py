"""HTTP 和 WebSocket 共用的回环代理与同源访问策略。

实现：连接对端始终必须为回环；Host 白名单来自 Django 设置，默认仍仅允许本机。
关联：production 设置允许指定公网 Host，session_socket 验证会话身份；不直接信任外部客户端。

目录：
- is_loopback：
  判断字符串地址是否属于回环网络。
- same_origin：
  比较浏览器 Origin 与请求的 scheme、host、端口。
- websocket_allowed：
  根据 ASGI scope 验证 WebSocket 的主机、连接地址及 Origin。

关键变量：
（无模块级变量。）
"""

from ipaddress import ip_address
from urllib.parse import urlsplit

from django.conf import settings
from django.http.request import validate_host


def is_loopback(address):
    """判断字符串地址是否属于回环网络。

    方法：使用标准库解释 IPv4/IPv6，不依赖字符串前缀匹配。
    返回：合法回环地址为 True，非法地址和非回环地址为 False。
    """
    try:
        return ip_address(address).is_loopback
    except ValueError:
        return False


def same_origin(origin, host, scheme):
    """比较浏览器 Origin 与请求的 scheme、host、端口。

    参数：origin 可为空，此时表示未发送 Origin 的非浏览器客户端。
    方法：保留既有严格比较规则，不自动改写端口或放宽来源限制。
    返回：来源匹配时为 True；不产生 I/O 副作用。
    """
    if origin is None:
        return True
    parsed = urlsplit(origin)
    return parsed.scheme == scheme and parsed.netloc == host and parsed.path in ("", "/")


def websocket_allowed(scope):
    """根据 ASGI scope 验证 WebSocket 的主机、连接地址及 Origin。

    输入：ASGI scope 中的 headers、client 和 scheme；Host 白名单读取 Django 设置。
    方法：检查配置的主机白名单与客户端回环地址，再应用同源规则。
    返回：可接受连接时为 True。生产代理须清除客户端转发地址并覆写可信协议头。
    约束：缺失 Host 明确拒绝；此函数不执行认证或 I/O，身份由上游 session_socket 负责。
    """
    headers = {
        key.decode("latin1").lower(): value.decode("latin1") for key, value in scope["headers"]
    }
    host = headers.get("host", "")
    hostname = urlsplit("http://" + host).hostname
    return (
        bool(hostname)
        and validate_host("[::1]" if hostname == "::1" else hostname, settings.ALLOWED_HOSTS)
        and is_loopback((scope.get("client") or ("", 0))[0])
        and same_origin(
            headers.get("origin"),
            host,
            "https" if scope.get("scheme") == "wss" else "http",
        )
    )
