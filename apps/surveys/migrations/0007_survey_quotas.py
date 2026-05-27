# Generated for the survey-quotas feature (feat/parity-quotas).
#
# Additive + backward-compatible: a new JSONField defaulting to [] (mirrors
# `endings` / `follow_ups` / `blocks` / `variables`). An absent/empty `quotas` array
# == today's exact behavior (no quota enforcement), so existing surveys are unaffected.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("surveys", "0006_survey_variables"),
    ]

    operations = [
        migrations.AddField(
            model_name="survey",
            name="quotas",
            field=models.JSONField(default=list),
        ),
    ]
