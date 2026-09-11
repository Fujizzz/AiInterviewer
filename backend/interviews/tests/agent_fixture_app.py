"""离线联调专用 ASGI 入口，必须显式指定；生产 config.asgi 不加载本模块。

目录：
（无本地函数或类定义。）

关键变量：
（无模块级变量。）

设计说明：
导入与副作用：application 复用 config.asgi 的入口；FixtureLLM 替换当前测试进程的
agent_session.BackendLLM。生产入口不会导入本测试模块。
"""

from config.asgi import application  # noqa: F401

from interviews import agent_session
from interviews.tests.agent_fixtures import FixtureLLM

agent_session.BackendLLM = FixtureLLM
