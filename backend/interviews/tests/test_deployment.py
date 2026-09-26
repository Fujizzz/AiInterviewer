"""职责：验证公网 Host 配置不会取消回环对端和同源边界。

实现：构造代理 ASGI scope，并用 Django 请求验证 HTTPS 代理协议；不调用外部服务。
关联：access.websocket_allowed、LocalOnlyMiddleware 和 config.production 的部署契约。

目录：
- DeploymentAccessTests：不访问数据库的部署访问策略回归集合。
- DeploymentAccessTests.scope：构造来自同机代理的 HTTPS WebSocket 请求。
- DeploymentAccessTests.test_proxy_origin_and_peer：允许指定同源代理，拒绝外站、远端与伪造 Host。
- DeploymentAccessTests.test_http_proxy_origin：HTTP 应用保留同源与回环限制。
- DeploymentAccessTests.test_local_defaults：默认开发配置继续拒绝公网 Host。

关键变量：
（无模块级变量。）
"""

from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, override_settings

from interviews.access import websocket_allowed
from interviews.middleware import LocalOnlyMiddleware


class DeploymentAccessTests(SimpleTestCase):
    """功能：验证代理访问边界；仅使用内存请求，不代表真实 Nginx 已完成认证。"""

    def scope(
        self,
        host="interview.example",
        origin="https://interview.example",
        peer="127.0.0.1",
        scheme="wss",
    ):
        """输入为主机、来源、对端和协议；返回 ASGI 检查所需字段，无 I/O 副作用。"""
        return {
            "headers": [(b"host", host.encode()), (b"origin", origin.encode())],
            "client": (peer, 12345),
            "scheme": scheme,
        }

    @override_settings(ALLOWED_HOSTS=["interview.example"])
    def test_proxy_origin_and_peer(self):
        """仅配置 Host 且同源的回环代理通过；逐项改变信任条件必须拒绝。"""
        self.assertTrue(websocket_allowed(self.scope()))
        for changes in (
            {"origin": "https://foreign.example"},
            {"peer": "192.0.2.10"},
            {"host": "foreign.example"},
            {"host": ""},
            {"scheme": "ws"},
        ):
            with self.subTest(changes=changes):
                self.assertFalse(websocket_allowed(self.scope(**changes)))

    @override_settings(
        ALLOWED_HOSTS=["interview.example"],
        SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO", "https"),
    )
    def test_http_proxy_origin(self):
        """模拟 Nginx 覆写协议头后的请求；同源允许，跨源与非回环拒绝。"""
        request = RequestFactory().get(
            "/",
            HTTP_HOST="interview.example",
            HTTP_ORIGIN="https://interview.example",
            HTTP_X_FORWARDED_PROTO="https",
            REMOTE_ADDR="127.0.0.1",
        )
        middleware = LocalOnlyMiddleware(HttpResponse)
        self.assertEqual(middleware(request).status_code, 200)
        request.META["HTTP_ORIGIN"] = "https://foreign.example"
        del request.headers  # Django 缓存 headers；模拟新请求前清除已读取的快照。
        self.assertEqual(middleware(request).status_code, 403)
        request.META["HTTP_ORIGIN"] = "https://interview.example"
        request.META["REMOTE_ADDR"] = "192.0.2.10"
        del request.headers
        self.assertEqual(middleware(request).status_code, 403)

    @override_settings(ALLOWED_HOSTS=["localhost", "127.0.0.1", "[::1]"])
    def test_local_defaults(self):
        """保留本机开发 Host 集合，验证生产 Host 不会在默认配置中隐式生效。"""
        self.assertFalse(websocket_allowed(self.scope()))
        self.assertTrue(
            websocket_allowed(self.scope(host="localhost", origin="http://localhost", scheme="ws"))
        )
