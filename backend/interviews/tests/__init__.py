"""后端自动化测试包。test_api 验证业务边界；test_streaming 验证无存储的协议生命周期。

目录：
（无本地函数或类定义。）

关键变量：
（无模块级变量。）

设计说明：
模块关系：test_api 验证业务事务；test_streaming 验证回传与无存储约束。
test_agent 验证 Agent 接入；agent_fixtures 与 agent_fixture_app 仅供显式离线注入。
"""
