from django.apps import AppConfig


class AccountsConfig(AppConfig):
    """PUBLIC-schema app (SHARED_APPS). Owns the custom `User` model
    (AUTH_USER_MODEL = 'accounts.User'). See contracts/models.md §1.1."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.accounts"
    label = "accounts"
