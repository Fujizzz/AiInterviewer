"""Responsibilities: 定义安全模块的显式策略配置，避免隐式修改主系统实验参数。
Implementation: 验证安全接口资源上限、动作白名单与必需语义检测的超时预算。
Related Modules: BehaviorEngine 消费策略；调用方按业务阶段提供策略实例。

Declaration Index:
- SecurityPolicy: 调用方必须明确提供的策略。
- SecurityPolicy.check_configuration: 拒绝重复动作配置。

Variable Index:
None

Configuration:
SecurityPolicy 的 policy_version 用于审计；max_scan_chars 限制序列化完整行为请求的总字符数。
allowed_actions 是补充白名单，不替代后端授权；所有准入通过的请求必须经过语义检测。
semantic_timeout_seconds 是安全检测时限，与业务模型的超时及预算无关。
"""

from pydantic import Field, model_validator

from shared.contracts.security import Identifier, SecurityModel


class SecurityPolicy(SecurityModel):
    """功能：保存不可变策略；输入：全部显式配置；约束：不包含业务评分、模型或流量限额默认值。"""

    policy_version: Identifier
    max_scan_chars: int = Field(gt=0, le=200_000)
    allowed_actions: tuple[Identifier, ...]
    semantic_timeout_seconds: float = Field(gt=0, le=120, allow_inf_nan=False)

    @model_validator(mode="after")
    def check_configuration(self) -> "SecurityPolicy":
        """功能：验证配置；输入：实例字段；输出：自身；逻辑：重复名单视为配置错误，不自动去重。"""
        if len(set(self.allowed_actions)) != len(self.allowed_actions):
            raise ValueError("allowed_actions must be unique")
        return self
