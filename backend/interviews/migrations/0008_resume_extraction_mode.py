"""职责：记录简历版本实际使用的提取模式，不猜测已有记录的处理路径。
实现：添加允许空值的展示字段；旧记录留空，新解析写入 traditional 或 advanced。
关联：ResumeVersion 与版本解析 API，依赖已有简历版本表。
目录：
- Migration：添加模式字段；逆迁移仅删除此列，不更改简历正文。
关键变量：
（无模块级变量。）
配置说明：
dependencies 定义迁移顺序，operations 添加列；不调用模型或修改模型超时。
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    """功能：增加模式元数据；逻辑：旧行默认空；约束：不推断旧模式或改写已存正文。"""

    dependencies = [
        ("interviews", "0007_resume_versions"),
    ]

    operations = [
        migrations.AddField(
            model_name="resumeversion",
            name="extraction_mode",
            field=models.CharField(blank=True, max_length=16),
        ),
    ]
