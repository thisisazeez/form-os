"""
Static catalog of DEFERRED integrations — descriptive metadata only, NO connect buttons.

These integrations need third-party OAuth/app credentials the product owner must provision; per
the brief we do NOT ship dead "Connect" buttons. The hub renders these as read-only cards so the
feature set is visible, and the ``needs`` line documents exactly what each requires.

NOTE: Zapier / Make / n8n are listed here for completeness, but they are effectively ALREADY
functional today via the shipped generic Webhooks integration ("Webhooks by Zapier" / Make's
"Custom webhook" / n8n's "Webhook" trigger all just consume our signed webhook). The hub points
that out and links to the Webhooks tab.
"""
from __future__ import annotations

DEFERRED_INTEGRATIONS = [
    {
        "key": "google_sheets",
        "name": "Google Sheets",
        "icon": "table",
        "description": "Append each new response as a row in a spreadsheet.",
        "needs": (
            "A Google Cloud project with an OAuth 2.0 Client ID + Secret, the "
            "spreadsheets and drive.file scopes, a verified OAuth consent screen, and our "
            "redirect URI registered."
        ),
    },
    {
        "key": "airtable",
        "name": "Airtable",
        "icon": "grid_view",
        "description": "Sync responses into an Airtable base.",
        "needs": (
            "An Airtable OAuth 2.0 client (Client ID + Secret, PKCE, registered redirect URI) "
            "from the Airtable developer hub — or a per-user Personal Access Token."
        ),
    },
    {
        "key": "notion",
        "name": "Notion",
        "icon": "description",
        "description": "Create a Notion database page per response.",
        "needs": (
            "A Notion integration token (internal integration) — or a public OAuth integration "
            "(Client ID + Secret + registered redirect URI) configured in Notion."
        ),
    },
    {
        "key": "zapier",
        "name": "Zapier / Make / n8n",
        "icon": "bolt",
        "description": "Automate downstream workflows on survey events.",
        "needs": (
            "Already works today via the Webhooks integration (use “Webhooks by Zapier”, "
            "Make’s Custom Webhook, or n8n’s Webhook trigger). A first-party Zapier app "
            "would additionally need a published Zapier App ID + developer account."
        ),
        "covered_by_webhooks": True,
    },
]
