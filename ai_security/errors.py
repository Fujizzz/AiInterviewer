"""Responsibilities: 定义行为检查与后端提交共享的状态变化异常。
Implementation: 提供无正文、无业务副作用的异常，使调用层区分过期快照与检测失败。
Related Modules: behavior 执行前重读、agent_records 原子保存及 agent_safety 错误码映射。

Declaration Index:
- SecurityContextChanged: 表示检查后的状态、权限或提案已经改变。

Variable Index:
None
"""


class SecurityContextChanged(RuntimeError):
    """功能：阻止过期放行；逻辑：由刷新或提交核对抛出；约束：不提供重试或回退行为。"""
