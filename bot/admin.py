from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import render
from django.urls import path, reverse
from django.utils.html import format_html

from .models import ChatMessage, Question
from .services import TelegramError, format_answer_for_user, mark_answered, save_question_to_faq, send_telegram_message


def short_chat_id(chat_id):
    return str(chat_id)[-6:]


@admin.action(description="ذخیره سؤالات پاسخ‌داده‌شده در پرسش‌های متداول (ربات از آن‌ها استفاده می‌کند)")
def save_to_faq(modeladmin, request, queryset):
    saved = sum(save_question_to_faq(q) for q in queryset.filter(status=Question.Status.ANSWERED))
    modeladmin.message_user(request, f"{saved} سؤال به پایگاه دانش اضافه شد.")


@admin.action(description="بستن سؤال (نیازی به پاسخ نیست)")
def dismiss(modeladmin, request, queryset):
    count = queryset.filter(status=Question.Status.PENDING).update(status=Question.Status.DISMISSED)
    modeladmin.message_user(request, f"{count} سؤال بسته شد.")


@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    list_display = ("short_question", "user_display", "status", "answered_by", "saved_to_faq", "created_at")
    list_filter = ("status", "saved_to_faq")
    search_fields = ("question", "answer", "username", "full_name")
    readonly_fields = (
        "chat_id", "user_id", "username", "full_name", "question", "reason",
        "answered_by", "answered_at", "saved_to_faq", "admin_message_id", "created_at",
    )
    fields = ("question", "reason", "full_name", "username", "status", "answer", "answered_by", "answered_at",
              "saved_to_faq", "chat_id", "user_id", "admin_message_id", "created_at")
    actions = [save_to_faq, dismiss]

    @admin.display(description="سؤال")
    def short_question(self, obj):
        return obj.question[:80]

    @admin.display(description="کاربر")
    def user_display(self, obj):
        return obj.user_display

    def has_add_permission(self, request):
        return False  # questions are created by the bot

    def save_model(self, request, obj, form, change):
        answer = obj.answer.strip()
        newly_answered = answer and "answer" in form.changed_data
        if not newly_answered:
            super().save_model(request, obj, form, change)
            return
        obj.answer = answer
        try:
            send_telegram_message(obj.chat_id, format_answer_for_user(obj))
        except TelegramError as exc:
            # Keep the draft answer but leave the question pending so it can be resent.
            obj.status = Question.Status.PENDING
            super().save_model(request, obj, form, change)
            messages.error(request, f"پاسخ ذخیره شد اما برای کاربر ارسال نشد: {exc}")
            return
        mark_answered(obj, answer, request.user.get_username())
        messages.success(request, "پاسخ در تلگرام برای کاربر ارسال شد.")


@admin.register(ChatMessage)
class ChatMessageAdmin(admin.ModelAdmin):
    """Flat message list; each row links to the whole conversation shown as a chat thread."""

    list_display = ("conversation_link", "channel", "role", "short_text", "escalated", "created_at")
    list_display_links = None
    list_filter = ("channel", "role", "escalated")
    search_fields = ("text",)

    @admin.display(description="گفتگو")
    def conversation_link(self, obj):
        url = reverse("admin:bot_chatmessage_conversation", args=[obj.channel, obj.chat_id])
        return format_html('<a href="{}">مشاهده گفتگو ({})</a>', url, short_chat_id(obj.chat_id))

    @admin.display(description="متن پیام")
    def short_text(self, obj):
        return obj.text[:100]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def get_urls(self):
        custom = [path(
            "conversation/<str:channel>/<str:chat_id>/",
            self.admin_site.admin_view(self.conversation_view),
            name="bot_chatmessage_conversation",
        )]
        return custom + super().get_urls()

    def conversation_view(self, request, channel, chat_id):
        if not self.has_view_permission(request):
            raise PermissionDenied
        try:
            chat_id = int(chat_id)
        except ValueError:
            raise Http404 from None
        thread = list(ChatMessage.objects.filter(channel=channel, chat_id=chat_id).order_by("created_at", "id"))
        if not thread:
            raise Http404
        context = {
            **self.admin_site.each_context(request),
            "title": f"گفتگوی {short_chat_id(chat_id)}",
            "opts": self.model._meta,
            "thread": thread,
            "channel_label": ChatMessage.Channel(channel).label if channel in ChatMessage.Channel.values else channel,
            "chat_id": chat_id,
            "escalated_count": sum(m.escalated for m in thread),
            "list_url": reverse("admin:bot_chatmessage_changelist"),
        }
        return render(request, "admin/bot/chatmessage/conversation.html", context)
