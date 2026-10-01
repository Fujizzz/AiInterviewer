"""职责：为简历版本新增独立编辑稿的来源关系、单元文本和用户确认推荐槽位。
实现：旧记录的来源为空、JSON 字段为空字典；不推断旧资料或改写原件/提取正文。
关联：ResumeVersion、resume_editor 与本人版本 API；依赖 0008。
目录：
- Migration：添加来源关系和编辑内容字段，逆迁移删除新增列。
关键变量：
（无模块级变量。）

配置说明：
Migration.dependencies 规定顺序；operations 添加两个 PROTECT 自关联与两个 JSON 列。

约束：
逆迁移会丢失编辑结构/来源元数据；编辑稿 text 仍在原有列，不进行模型调用或训练改动。
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """功能：添加编辑快照元数据；逻辑：默认不回填；约束：保护原件与基线被引用时的删除。"""

    dependencies = [
        ("interviews", "0008_resume_extraction_mode"),
    ]

    operations = [
        migrations.AddField(
            model_name="resumeversion",
            name="edited_from",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="derived_versions",
                to="interviews.resumeversion",
            ),
        ),
        migrations.AddField(
            model_name="resumeversion",
            name="recommendation_slots",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="resumeversion",
            name="source_version",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="editions",
                to="interviews.resumeversion",
            ),
        ),
        migrations.AddField(
            model_name="resumeversion",
            name="units",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
