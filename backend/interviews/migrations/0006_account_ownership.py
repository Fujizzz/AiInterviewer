"""职责：为面试和练习增加用户归属，不改动既有状态、答案或评分。

实现：添加允许 null 的用户外键和索引，旧数据保持未归属，删除用户需先显式处理其记录。
关联：auth 用户表、AgentInterview 与 PracticeSession；依赖前序迁移与可替换用户模型。

目录：
- Migration：可逆地增加两个 owner 字段；逆迁移只删除归属列，不删除业务记录。

关键变量：
（无模块级变量。）

配置说明：
dependencies 固定 schema 先后顺序，operations 的 null=True 使旧记录无需自动认领。
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    """为两类会话添加用户外键；不回填旧记录，不改变实验数据和参数。"""

    dependencies = [
        ("interviews", "0005_agent_request_order"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="agentinterview",
            name="owner",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="agent_interviews",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="practicesession",
            name="owner",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="practice_sessions",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
