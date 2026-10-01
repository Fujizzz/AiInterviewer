"""职责：让请求历史索引覆盖完整的稳定分页排序，不修改业务数据。

实现：移除旧两列索引，增加 interview、created_at、id 三列复合索引。
关联：agent_models.AgentRequest.Meta；依赖 0004_agent_persistence。

目录：
- Migration：声明替换请求历史索引的可逆 schema 操作。

关键变量：
（无模块级变量。）

配置说明：
dependencies 固定前序迁移；operations 只改变索引，不更改请求顺序、状态、数据或唯一约束。
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    """声明替换请求历史索引的可逆 schema 操作。"""

    dependencies = [
        ("interviews", "0004_agent_persistence"),
    ]

    operations = [
        migrations.RemoveIndex(
            model_name="agentrequest",
            name="agent_request_history",
        ),
        migrations.AddIndex(
            model_name="agentrequest",
            index=models.Index(
                fields=["interview", "created_at", "id"], name="agent_request_history"
            ),
        ),
    ]
