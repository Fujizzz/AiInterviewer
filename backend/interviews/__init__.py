"""面试后端应用包。模型、事务、REST 与流式传输各自分层，不承担 Agent 决策。

目录：
- models、services：业务模型与事务；api：REST 适配；errors：异常映射。
- access、middleware：访问检查；streaming：回传协议；demo：静态资源。
- migrations、tests：数据库演进与隔离验证；本包不定义运行时函数。
"""
