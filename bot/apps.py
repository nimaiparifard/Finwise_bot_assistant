from django.apps import AppConfig


class BotConfig(AppConfig):
    name = "bot"
    verbose_name = "ربات تلگرام"

    def ready(self):
        # Django's blank choice ("- Select an option -", used by the admin actions drop-down and
        # forms) has no Persian translation; the documented hook is to overwrite it here.
        from django.db.models import fields

        fields.BLANK_CHOICE_LABEL = "— انتخاب کنید —"
