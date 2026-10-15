import os

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from bot.telegram_bot import build_application


class Command(BaseCommand):
    help = "Run the FinWise Telegram bot (long polling)."

    def handle(self, *args, **options):
        try:
            app = build_application()
        except RuntimeError as exc:
            raise CommandError(str(exc)) from exc
        if settings.LLM_PROVIDER not in ("openai", "anthropic"):
            raise CommandError(f"LLM_PROVIDER must be 'openai' or 'anthropic', not {settings.LLM_PROVIDER!r}.")
        if settings.LLM_PROVIDER == "openai":
            if not (settings.OPENAI_API_KEY or os.getenv("OPENAI_API_KEY")):
                raise CommandError("OPENAI_API_KEY is not set.")
            endpoint = settings.OPENAI_BASE_URL or "https://api.openai.com/v1"
            self.stdout.write(f"LLM: OpenAI-compatible, model {settings.OPENAI_MODEL} at {endpoint}")
        else:
            if not (os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN")):
                self.stderr.write(self.style.WARNING(
                    "ANTHROPIC_API_KEY is not set; the bot will only work if an `ant auth login` profile is active."
                ))
            self.stdout.write(f"LLM: Anthropic, model {settings.ANTHROPIC_MODEL}")
        if not settings.TELEGRAM_ADMIN_CHAT_ID:
            self.stderr.write(self.style.WARNING(
                "TELEGRAM_ADMIN_CHAT_ID is not set; unanswered questions will only appear in the admin panel. "
                "Send /chatid to the bot from the admin chat to find its id."
            ))
        self.stdout.write(self.style.SUCCESS("Bot is running. Press Ctrl+C to stop."))
        app.run_polling(allowed_updates=["message"])
