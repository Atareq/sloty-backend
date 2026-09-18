from django.apps import AppConfig


class CommonConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.common"

    def ready(self):
        # Registers the deploy-time checks in apps/common/checks.py.
        from apps.common import checks  # noqa: F401
