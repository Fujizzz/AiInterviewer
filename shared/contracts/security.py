"""职责：提供当前行为边界契约共用的严格类型及带来源的文本片段。
实现：冻结模型、禁止额外字段和隐式转换；校验受众与派生引用不重复。
关联：behavior 契约及后端输入输出网关使用；不含文本攻击分类的请求或结果。

目录：
- SecurityModel：共享严格校验配置。
- SecurityContent：标记来源、受众及派生来源的文本。
- SecurityContent.check_provenance：拒绝重复受众与重复来源引用。

关键变量：
- Identifier：限定审计标识符的长度和字符集。
- Recipient：后端允许的数据受众类型。

约束说明：
SecurityModel.model_config 禁止未知字段、非有限数值和实例修改，并重验模型实例。
来源和受众由后端提供；本契约不认证来源，也不能使嵌套字典不可变。
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Identifier = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")]
Recipient = Literal["candidate", "staff", "internal"]


class SecurityModel(BaseModel):
    """功能：统一契约校验；逻辑：严格、冻结、拒绝未知字段；约束：不验证身份真实性。"""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        revalidate_instances="always",
        allow_inf_nan=False,
    )


class SecurityContent(SecurityModel):
    """功能：保存来源；逻辑：readable_by 限定受众，derived_from 指向父片段；约束：仅支持文本。"""

    content_id: Identifier
    source: Literal["user", "resume", "job", "rag", "tool_result", "memory", "model_output"]
    text: str = Field(max_length=200_000)
    readable_by: tuple[Recipient, ...] = Field(min_length=1, max_length=3)
    derived_from: tuple[Identifier, ...] = Field(default=(), max_length=64)

    @model_validator(mode="after")
    def check_provenance(self) -> "SecurityContent":
        """功能：核对来源声明；输入：受众/父片段引用；输出：自身；标签只能由后端赋予，不认证来源。"""
        if len(set(self.readable_by)) != len(self.readable_by):
            raise ValueError("readable_by must be unique")
        if len(set(self.derived_from)) != len(self.derived_from):
            raise ValueError("derived_from must be unique")
        return self
