# Generated for the survey-quotas feature (feat/parity-quotas).
#
# Additive + backward-compatible: two new columns on Response capturing the over-quota state set
# at finalize. `over_quota` defaults to False (existing rows are not over-quota);
# `quota_full_ending_id` is nullable (the routed ending, or NULL for the built-in screen / not
# over-quota). A survey with no quotas never sets either, so existing behavior is unaffected.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("responses", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="response",
            name="over_quota",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="response",
            name="quota_full_ending_id",
            field=models.CharField(blank=True, max_length=64, null=True),
        ),
    ]
