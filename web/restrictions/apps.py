from django.apps import AppConfig


class RestrictionsConfig(AppConfig):
    name = "restrictions"
    verbose_name = "Core banking restrictions"

    def ready(self):
        from . import signals  # noqa: F401
