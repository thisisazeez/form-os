"""
Template assignment tag for conditional UI — ``{% can user "action" obj as flag %}``
(rbac.md §7 / §8.3).

Implemented as a Django **assignment tag** (``@register.simple_tag``) because Django template
**filters take only one argument** — ``user|can:"edit":survey`` is impossible (PLAN §5). The
tag delegates to the SAME ``apps.common.permissions.can()`` as the DRF + HTMX surfaces; there
is no second policy here.

Template authorization is UI-ONLY (hide/show); it is NOT a security boundary (rbac.md §8.3).
The DRF/HTMX layers are the real gate. Reviewers verify no endpoint relies solely on this tag.

Usage:
    {% load rbac %}
    {% can request.user "edit_survey" survey as may_edit %}
    {% if may_edit %}<button>Edit</button>{% endif %}

    {# org-level / collection action: omit obj (or pass None) #}
    {% can request.user "invite_member" as may_invite %}

Fail-soft for UI: an unknown action would make ``can()`` raise ValueError. To keep a broken
template from 500-ing a whole page over a UI affordance, we catch ValueError and resolve to
False (deny). All OTHER behavior (anonymous → False, real authz) is delegated unchanged.
"""
from __future__ import annotations

from django import template

from apps.common.permissions import can as _can

register = template.Library()


@register.simple_tag(name="can")
def can_tag(user, action, obj=None):
    """Resolve ``can(user, action, obj)`` for use with ``as <var>`` in templates.

    Returns a bool. An unknown action (ValueError from can()) resolves to False rather than
    raising, so a typo in a template can't take down the page (UI-only gate). Real
    authorization decisions are delegated verbatim to can().
    """
    try:
        return bool(_can(user, action, obj))
    except ValueError:
        # Unknown action — deny in the UI rather than 500 the page. (The server-side gate
        # would raise ValueError loudly on the same typo, surfacing the bug there.)
        return False
