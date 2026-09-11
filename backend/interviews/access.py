"""HTTP 和 WebSocket 共用的本机访问策略。

目录：is_loopback（客户端地址）；same_origin（来源匹配）；
websocket_allowed（WebSocket 握手检查）。
本模块为无状态判定函数，不读取数据库、不修改请求，也不建立网络连接。
"""

from ipaddress import ip_address
from urllib.parse import urlsplit


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

    方法：同时检查显式本地主机白名单与客户端回环地址，再应用同源规则。
    返回：可接受连接时为 True。Host 白名单防止任意 DNS 别名获得访问资格。
    """
    headers = {
        key.decode("latin1").lower(): value.decode("latin1") for key, value in scope["headers"]
    }
    host = headers.get("host", "")
    hostname = urlsplit("http://" + host).hostname
    return (
        hostname in ("localhost", "127.0.0.1", "::1")
        and is_loopback((scope.get("client") or ("", 0))[0])
        and same_origin(
            headers.get("origin"),
            host,
            "https" if scope.get("scheme") == "wss" else "http",
        )
    )
