"""Responsibilities: Seed the two stable default interview questions.
Implementation: Use historical models and get_or_create so reapplication preserves any existing
edits; reversal is a no-op.
Related Modules: The initial interviews schema and Django's migration runner.
Declaration Index:
- seed: Insert the stable question identifiers and order into the migration database.
- Migration: Run the seed function after the initial schema migration.
Variable Index:
- QUESTIONS: Stable identifiers and English prompt text for the two initial questions.
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
    """Functionality: Insert the two stable default questions into the migration database.
    Inputs: Historical app registry and schema editor.
    Outputs: Creates missing question rows with their stable identifiers and positions.
    Logic: Resolve the historical Question model and use get_or_create.
    Constraints: Existing rows are preserved; the reverse operation is a no-op.
    """
    Question = apps.get_model("interviews", "Question")
    for position, (pk, text) in enumerate(QUESTIONS, start=1):
        Question.objects.using(schema_editor.connection.alias).get_or_create(
            id=pk, defaults={"text": text, "position": position}
        )


class Migration(migrations.Migration):
    """Functionality: Apply the question seed after the initial schema.
    Inputs: Prior migration state.
    Outputs: Runs seed during forward migration.
    Logic: Declare the initial migration dependency and seed operation.
    Constraints: Reversal intentionally preserves potentially edited questions.
    """

    dependencies = [("interviews", "0001_initial")]
    # Do not delete potentially edited user questions when this data migration is reversed.
    operations = [migrations.RunPython(seed, reverse_code=migrations.RunPython.noop)]
