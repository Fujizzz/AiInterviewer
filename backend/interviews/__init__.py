"""面试后端应用包。模型、事务、REST 与流式传输各自分层，不承担 Agent 决策。

目录：
（无本地函数或类定义。）

关键变量：
（无模块级变量。）

设计说明：
模块关系：models/services 管理练习数据，api 适配 REST。
agent_provider/agent_session/agent_socket 接入 MVP；streaming 处理回传。
access/middleware 限制来源；demo、migrations、tests 管理资源、迁移和验证。
"""
