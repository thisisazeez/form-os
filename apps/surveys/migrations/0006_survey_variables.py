# Generated for the survey-variables + calculate-action feature (feat/parity-variables).
#
# Additive + backward-compatible: a new JSONField defaulting to [] (mirrors
# `endings` / `follow_ups` / `blocks`). An absent/empty `variables` array == today's
# exact behavior (no variable replay), so existing surveys are unaffected.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("surveys", "0005_survey_blocks"),
    ]

    operations = [
        migrations.AddField(
            model_name="survey",
            name="variables",
            field=models.JSONField(default=list),
        ),
    ]
