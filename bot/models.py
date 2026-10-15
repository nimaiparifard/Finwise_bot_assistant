from django.db import models


class ChatMessage(models.Model):
    """Conversation history per Telegram chat; recent turns are sent to the LLM as context."""

    class Role(models.TextChoices):
        USER = "user", "کاربر"
        ASSISTANT = "assistant", "ربات / کارشناس"

    class Channel(models.TextChoices):
        TELEGRAM = "telegram", "تلگرام"
        WEB = "web", "چت وب‌سایت"

    channel = models.CharField("کانال", max_length=20, choices=Channel.choices, default=Channel.TELEGRAM, db_index=True)
    chat_id = models.BigIntegerField("شناسه گفتگو", db_index=True)
    role = models.CharField("نقش", max_length=20, choices=Role.choices)
    text = models.TextField("متن پیام")
    escalated = models.BooleanField(
        "ارجاع شده", default=False, help_text="ربات نتوانست پاسخ دهد و سؤال را به کارشناس ارجاع داد."
    )
    created_at = models.DateTimeField("زمان", auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "پیام گفتگو"
        verbose_name_plural = "پیام‌های گفتگو"

    def __str__(self):
        return f"{self.chat_id} {self.role}: {self.text[:60]}"


class Question(models.Model):
    """A user question the LLM could not answer, waiting for (or answered by) an admin."""

    class Status(models.TextChoices):
        PENDING = "pending", "در انتظار پاسخ"
        ANSWERED = "answered", "پاسخ داده شد"
        DISMISSED = "dismissed", "بسته شد"

    chat_id = models.BigIntegerField("شناسه گفتگو", db_index=True)
    user_id = models.BigIntegerField("شناسه کاربر", null=True, blank=True)
    username = models.CharField("نام کاربری", max_length=100, blank=True)
    full_name = models.CharField("نام کاربر", max_length=200, blank=True)

    question = models.TextField("سؤال")
    reason = models.TextField("دلیل ارجاع", blank=True, help_text="چرا ربات نتوانست پاسخ دهد.")
    status = models.CharField("وضعیت", max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True)
    answer = models.TextField(
        "پاسخ", blank=True,
        help_text="پاسخ را بنویسید و ذخیره کنید؛ بلافاصله در تلگرام برای کاربر ارسال می‌شود.",
    )
    answered_by = models.CharField("پاسخ‌دهنده", max_length=150, blank=True)
    answered_at = models.DateTimeField("زمان پاسخ", null=True, blank=True)
    saved_to_faq = models.BooleanField("ذخیره در پرسش‌های متداول", default=False)

    # Message id of the notification in the admin Telegram chat (admins answer by replying to it).
    admin_message_id = models.BigIntegerField("شناسه پیام در گروه مدیران", null=True, blank=True, db_index=True)
    created_at = models.DateTimeField("زمان ثبت", auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "سؤال کاربر"
        verbose_name_plural = "سؤالات کاربران"

    def __str__(self):
        return f"#{self.short_id} {self.question[:60]}"

    @property
    def short_id(self):
        return str(self.pk)[-6:] if self.pk else "جدید"

    @property
    def user_display(self):
        name = self.full_name or "—"
        return f"{name} (@{self.username})" if self.username else name
