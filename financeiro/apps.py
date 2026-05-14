from django.apps import AppConfig


class FinanceiroConfig(AppConfig):
    """Configuracao basica do app financeiro."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "financeiro"
