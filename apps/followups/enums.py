"""
followups enumerations.

``FollowUpDeliveryStatus`` is the lifecycle of one (follow-up, response) send attempt:
  - PENDING — row created; the async send task is enqueued but not yet run.
  - SENT    — the email was handed to the email backend successfully.
  - SKIPPED — the follow-up fired but no recipient email could be resolved (e.g. a
              {type:'contact'} recipient on an anonymous response, or a responseField
              that the respondent left blank). NOT a failure — there was simply no one to
              email; recorded so the dashboard can show "fired, nobody to send to".
  - FAILED  — the send raised (transport/render error). The error is recorded; finalize is
              NOT affected (follow-up failures must never break the response submission).

The trigger-event vocabulary mirrors survey-schema.json #/$defs/followUpTrigger.type and
#/$defs/followUpAction.type; only the values we implement in v1 are enumerated.
"""
from django.db import models


class FollowUpDeliveryStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    SENT = "sent", "Sent"
    SKIPPED = "skipped", "Skipped"
    FAILED = "failed", "Failed"


class FollowUpTriggerType(models.TextChoices):
    RESPONSE_FINISHED = "responseFinished", "Response finished"


class FollowUpActionType(models.TextChoices):
    SEND_EMAIL = "sendEmail", "Send email"


class FollowUpRecipientType(models.TextChoices):
    CONTACT = "contact", "Associated contact"
    FIXED_EMAIL = "fixedEmail", "Fixed email address"
    RESPONSE_FIELD = "responseField", "Response field (respondent)"
