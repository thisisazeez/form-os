# Generated for the question-blocks grouping overlay (feat/parity-blocks).
#
# Additive + backward-compatible: a new JSONField defaulting to [] (mirrors
# `endings` / `follow_ups`). An absent/empty `blocks` array == today's exact flat
# behavior, so existing surveys are unaffected.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("surveys", "0004_survey_follow_ups"),
    ]

    operations = [
        migrations.AddField(
            model_name="survey",
            name="blocks",
            field=models.JSONField(default=list),
        ),
    ]
