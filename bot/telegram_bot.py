"""
Telegram bot: answers course questions with the LLM and escalates the rest to admins.

Flow for a user message:
  1. Store it in the chat history.
  2. Ask Claude with the knowledge base + recent history.
  3. If Claude can answer -> reply. Otherwise -> create a Question, tell the user it was forwarded,
     and post it to TELEGRAM_ADMIN_CHAT_ID.
Admins answer by *replying* to that post in the admin chat (or from the Django admin panel);
the answer is delivered to the user.
"""

import logging

from asgiref.sync import sync_to_async
from django.conf import settings
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from courses.models import Course

from . import llm
from .knowledge import build_llm_context, recent_history
from .models import ChatMessage, Question
from .services import format_answer_for_user, mark_answered, save_question_to_faq, split_message

logger = logging.getLogger(__name__)

WELCOME = (
    "سلام! 👋 به ربات مرکز آموزش فین‌وایز خوش آمدید.\n\n"
    "هر سؤالی درباره دوره‌ها دارید بپرسید؛ مثلاً:\n"
    "- چه دوره‌هایی در حال ثبت‌نام است؟\n"
    "- قیمت دوره تجزیه و تحلیل گزارش‌های مالی چقدر است؟\n"
    "- چطور ثبت‌نام کنم؟\n\n"
    "/courses فهرست دوره‌های در حال ثبت‌نام"
)
ADMIN_HELP = (
    "دستورات گروه مدیران:\n"
    "- برای پاسخ به یک سؤال، روی پیام ارجاع‌شده «ریپلای» کنید. اگر می‌خواهید پاسخ در پرسش‌های متداول هم ذخیره شود، "
    "پاسخ را با +faq شروع کنید.\n"
    "- /pending — فهرست سؤالات بی‌پاسخ\n"
    "- /chatid — نمایش شناسه این گفتگو"
)


def admin_chat_id():
    value = str(settings.TELEGRAM_ADMIN_CHAT_ID).strip()
    return int(value) if value.lstrip("-").isdigit() else None


def is_admin_chat(update: Update):
    return update.effective_chat is not None and update.effective_chat.id == admin_chat_id()


async def reply_long(message, text):
    for chunk in split_message(text):
        await message.reply_text(chunk, disable_web_page_preview=True)


# --- Database helpers (Django ORM is synchronous) -----------------------------

@sync_to_async
def db_save_message(chat_id, role, text, escalated=False):
    ChatMessage.objects.create(chat_id=chat_id, role=role, text=text, escalated=escalated)


@sync_to_async
def db_context(chat_id):
    history = recent_history(chat_id, settings.LLM_HISTORY_TURNS)
    knowledge_text, retrieved = build_llm_context(history)
    return knowledge_text, history, retrieved


@sync_to_async
def db_create_question(update: Update, text, reason):
    user = update.effective_user
    return Question.objects.create(
        chat_id=update.effective_chat.id,
        user_id=user.id if user else None,
        username=(user.username or "") if user else "",
        full_name=(user.full_name or "") if user else "",
        question=text,
        reason=reason,
    )


@sync_to_async
def db_set_admin_message(question, message_id):
    question.admin_message_id = message_id
    question.save(update_fields=["admin_message_id"])


@sync_to_async
def db_question_by_admin_message(message_id):
    return Question.objects.filter(admin_message_id=message_id).first()


@sync_to_async
def db_mark_answered(question, answer, answered_by, add_to_faq):
    mark_answered(question, answer, answered_by)
    if add_to_faq:
        save_question_to_faq(question)


@sync_to_async
def db_pending_questions(limit=20):
    return list(Question.objects.filter(status=Question.Status.PENDING).order_by("created_at")[:limit])


@sync_to_async
def db_open_courses():
    return list(Course.objects.filter(is_active=True, registration_open=True).order_by("course_type", "title"))


# --- Handlers --------------------------------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(ADMIN_HELP if is_admin_chat(update) else WELCOME)


async def chat_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"شناسه گفتگو: {update.effective_chat.id}")


async def list_courses(update: Update, context: ContextTypes.DEFAULT_TYPE):
    courses = await db_open_courses()
    if not courses:
        await update.message.reply_text("در حال حاضر دوره‌ای در حال ثبت‌نام نیست.")
        return
    lines = ["📚 دوره‌های در حال ثبت‌نام:\n"]
    for course in courses:
        kind = "آنلاین" if course.course_type == Course.CourseType.ONLINE else "آفلاین"
        when = f" | شروع: {course.start_date}" if course.start_date else ""
        lines.append(f"• {course.title} ({kind})\n  💰 {course.price_display}{when}\n  🔗 {course.url}\n")
    await reply_long(update.message, "\n".join(lines))


async def pending(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin_chat(update):
        return
    questions = await db_pending_questions()
    if not questions:
        await update.message.reply_text("سؤال بی‌پاسخی وجود ندارد. ✅")
        return
    lines = [f"⏳ {len(questions)} سؤال بی‌پاسخ (قدیمی‌ترین اول):"]
    lines += [f"\n#{q.short_id} — {q.user_display}\n{q.question}" for q in questions]
    lines.append("\nدر پنل مدیریت پاسخ دهید یا روی پیام اصلی هر سؤال در همین گروه ریپلای کنید.")
    await reply_long(update.message, "\n".join(lines))


async def handle_user_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    text = (message.text or "").strip()
    if not text:
        return
    chat = update.effective_chat
    await context.bot.send_chat_action(chat.id, ChatAction.TYPING)

    await db_save_message(chat.id, ChatMessage.Role.USER, text)
    knowledge_text, history, retrieved = await db_context(chat.id)
    result = await llm.answer_question(knowledge_text, history, retrieved)

    await reply_long(message, result.answer)
    await db_save_message(chat.id, ChatMessage.Role.ASSISTANT, result.answer, escalated=not result.can_answer)
    if not result.can_answer:
        await escalate(update, context, text, result.reason)


async def escalate(update: Update, context: ContextTypes.DEFAULT_TYPE, text, reason):
    question = await db_create_question(update, text, reason)
    target = admin_chat_id()
    if target is None:
        logger.warning("TELEGRAM_ADMIN_CHAT_ID not set; question %s is only visible in the admin panel.", question.pk)
        return
    notice = (
        f"❓ سؤال جدید #{question.short_id}\n"
        f"از: {question.user_display} — شناسه کاربر {question.user_id}\n\n"
        f"{text}\n\n"
        f"🤖 دلیل پاسخ ندادن ربات: {reason or '-'}\n\n"
        "↩️ برای پاسخ، روی همین پیام ریپلای کنید (برای ذخیره در پرسش‌های متداول، با +faq شروع کنید)."
    )
    try:
        sent = await context.bot.send_message(target, notice)
    except Exception:  # noqa: BLE001 - the question is still stored for the admin panel
        logger.exception("Could not notify admin chat %s", target)
        return
    await db_set_admin_message(question, sent.message_id)


async def handle_admin_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    question = await db_question_by_admin_message(message.reply_to_message.message_id)
    if question is None:
        return  # an ordinary conversation in the admin group

    answer = (message.text or "").strip()
    add_to_faq = answer.lower().startswith("+faq")
    if add_to_faq:
        answer = answer[4:].strip()
    if not answer:
        await message.reply_text("پاسخ خالی است؛ چیزی ارسال نشد.")
        return

    question.answer = answer
    try:
        for chunk in split_message(format_answer_for_user(question)):
            await context.bot.send_message(question.chat_id, chunk)
    except Exception as exc:  # noqa: BLE001 - report delivery failures back to the admin
        logger.exception("Could not deliver answer for question %s", question.pk)
        await message.reply_text(f"⚠️ ارسال پاسخ به کاربر ناموفق بود: {exc}")
        return

    admin = update.effective_user
    answered_by = f"telegram:{admin.username or admin.id}" if admin else "telegram"
    await db_mark_answered(question, answer, answered_by, add_to_faq)
    note = " و در پرسش‌های متداول ذخیره شد" if add_to_faq else ""
    await message.reply_text(f"✅ پاسخ برای {question.user_display} ارسال شد{note}.")


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Error while handling an update", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message and not is_admin_chat(update):
        await update.effective_message.reply_text("متأسفانه خطایی رخ داد؛ لطفاً دوباره تلاش کنید.")


def build_application():
    if not settings.TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured.")
    app = Application.builder().token(settings.TELEGRAM_BOT_TOKEN).build()

    private = filters.ChatType.PRIVATE
    admin_id = admin_chat_id()
    # An empty Chat filter matches nothing, so without an admin chat all private chats are users.
    admin_chat = filters.Chat(chat_id=admin_id) if admin_id is not None else filters.Chat()

    app.add_handler(CommandHandler(["start", "help"], start))
    app.add_handler(CommandHandler("chatid", chat_id))
    app.add_handler(CommandHandler("courses", list_courses))
    app.add_handler(CommandHandler("pending", pending, filters=admin_chat))
    app.add_handler(MessageHandler(admin_chat & filters.REPLY & filters.TEXT & ~filters.COMMAND, handle_admin_reply))
    # Users talk to the bot in private chats; the admin chat itself is never treated as a user.
    app.add_handler(MessageHandler(private & ~admin_chat & filters.TEXT & ~filters.COMMAND, handle_user_message))
    app.add_error_handler(on_error)
    return app

