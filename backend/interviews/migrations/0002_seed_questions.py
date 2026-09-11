"""题库种子迁移。使用稳定 UUID 初始化原有两道通用题，保留已有编辑。

目录：
- seed：
  功能：向 schema_editor 指定数据库写入两道稳定 ID 的通用题。
- Migration：
  依赖初始 schema 后执行种子写入；逆向不删除可能被用户修改的题目。

关键变量：
- QUESTIONS：
  既有两道种子题的稳定 UUID、文字与顺序；迁移不覆盖已有记录。

关键状态说明：
Migration.dependencies 指向初始建表迁移；operations 调用 seed。
逆向操作采用既有 noop，保留已有题目。
"""

from django.db import migrations

QUESTIONS = [
    (
        "cc9b969c-76e4-4934-966e-8039537f9921",
        "What are your career goals for the next three years?",
    ),
    ("7ec8f854-d29c-4f43-b433-f34b388a5611", "Talk about your most recent project."),
]


def seed(apps, schema_editor):
    """功能：向 schema_editor 指定数据库写入两道稳定 ID 的通用题。
    方法：使用历史模型和 get_or_create，重复应用不覆盖已有内容。"""
    Question = apps.get_model("interviews", "Question")
    for position, (pk, text) in enumerate(QUESTIONS, start=1):
        Question.objects.using(schema_editor.connection.alias).get_or_create(
            id=pk, defaults={"text": text, "position": position}
        )


class Migration(migrations.Migration):
    """依赖初始 schema 后执行种子写入；逆向不删除可能被用户修改的题目。"""

    dependencies = [("interviews", "0001_initial")]
    # Do not delete potentially edited user questions when this data migration is reversed.
    operations = [migrations.RunPython(seed, reverse_code=migrations.RunPython.noop)]
