"""职责：验证生产访问边界及实际岗位推荐部署探针的成功、失败和清理契约。

实现：构造代理 ASGI scope，验证 HTTPS 代理协议；本机 LiveServer 与真实冻结模型
验证部署岗位探针的 Session/CSRF 请求和成功/失败清理，不访问生产数据库或外部服务。
关联：access.websocket_allowed、LocalOnlyMiddleware 和 config.production 的部署契约。

目录：
- DeploymentAccessTests：不访问数据库的部署访问策略回归集合。
- DeploymentAccessTests.scope：构造来自同机代理的 HTTPS WebSocket 请求。
- DeploymentAccessTests.test_proxy_origin_and_peer：允许指定同源代理，拒绝外站、远端与伪造 Host。
- DeploymentAccessTests.test_http_proxy_origin：HTTP 应用保留同源与回环限制。
- DeploymentAccessTests.test_local_defaults：默认开发配置继续拒绝公网 Host。
- DeploymentRecommendationTests：以隔离数据库和本机 HTTP 服务验证发布岗位验收。
- DeploymentRecommendationTests.test_live_catalog_and_cleanup：真实 100 岗排序成功且清理探针记录。
- DeploymentRecommendationTests.test_missing_catalog_fails_and_cleans_up：来源缺失明确失败仍清理。

关键变量：
（无模块级变量。）
"""

from pathlib import Path
from urllib.error import HTTPError

from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.http import HttpResponse
from django.test import LiveServerTestCase, RequestFactory, SimpleTestCase, override_settings

from deploy.smoke import verify_recommendations
from interviews.access import websocket_allowed
from interviews.middleware import LocalOnlyMiddleware
from interviews.resume_models import ResumeVersion


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


@override_settings(
    INTERVIEW_REQUIRE_LOGIN=True,
    ALLOWED_HOSTS=["47.239.50.129", "localhost"],
    SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO", "https"),
    STATIC_URL="/static/",
    MEDIA_URL="/media/",
)
class DeploymentRecommendationTests(LiveServerTestCase):
    """功能：验证发布验收真实 HTTP 行为与清理；逻辑：使用隔离库及本机线程服务。

    前提：只为 LiveServer 文件处理器提供静态/媒体 URL，不改生产路由或配置。
    约束：沿用 Session、CSRF 和原冻结模型，无认证/模型替身；此本机 WSGI 验证不代表
    生产 ASGI 或 Nginx 成功，真实发布另由相同探针检查运行中的服务。
    """

    def test_live_catalog_and_cleanup(self):
        """输入隔离用户库与完整实验目录；验证真实排序通过，账号/简历/会话均不残留。"""
        path = Path(__file__).resolve().parents[1] / "recommendation/data/experience-jobs.json"
        with override_settings(RECOMMENDATION_JOB_CATALOG=str(path)):
            verify_recommendations(self.live_server_url)
        self.assertEqual(get_user_model().objects.count(), 0)
        self.assertEqual(ResumeVersion.objects.count(), 0)
        self.assertEqual(Session.objects.count(), 0)

    @override_settings(RECOMMENDATION_JOB_CATALOG="")
    def test_missing_catalog_fails_and_cleans_up(self):
        """输入明确空目录配置；验证 HTTP 503 传播，失败路径仍删除所有探针记录，不回退。"""
        with self.assertRaises(HTTPError) as error:
            verify_recommendations(self.live_server_url)
        self.assertEqual(error.exception.code, 503)
        self.assertEqual(get_user_model().objects.count(), 0)
        self.assertEqual(ResumeVersion.objects.count(), 0)
        self.assertEqual(Session.objects.count(), 0)
