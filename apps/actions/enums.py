"""
actions — enums. The ActionClass.type taxonomy (mirrors Formbricks's actionClass types).
"""
from django.db import models


class ActionType(models.TextChoices):
    """How an ActionClass is fired by the widget at runtime.

    - ``code``     — the host app calls the JS SDK's ``track(<key>)`` explicitly; matched by
                     ``config.key`` (falls back to ``name``).
    - ``pageview`` — fires on a page view whose URL matches ``config.urlMatch`` (widget-side).
    - ``click``    — fires on a click matching ``config.cssSelector`` (widget-side).

    The SERVER stores the definition + (for ``track``) matches by name/key; the URL/selector
    matching for pageview/click is the widget's job (it sends the resolved action name/key to
    ``track``). Documented in the public ``track`` endpoint.
    """

    CODE = "code", "Code"
    PAGEVIEW = "pageview", "Page view"
    CLICK = "click", "Click"
