from django.contrib import admin, messages
from django.shortcuts import redirect
from django.urls import path, reverse

from .crawler import CRAWLED_FIELDS, crawl_site, save_crawl
from .models import Course, KnowledgeEntry


@admin.action(description="باز کردن قفل (اجازه به خزنده برای به‌روزرسانی)")
def unlock(modeladmin, request, queryset):
    count = queryset.update(locked=False)
    modeladmin.message_user(request, f"قفل {count} مورد باز شد.")


@admin.action(description="فعال‌سازی (نمایش برای ربات)")
def activate(modeladmin, request, queryset):
    modeladmin.message_user(request, f"{queryset.update(is_active=True)} مورد فعال شد.")


@admin.action(description="غیرفعال‌سازی (پنهان از ربات)")
def deactivate(modeladmin, request, queryset):
    modeladmin.message_user(request, f"{queryset.update(is_active=False)} مورد غیرفعال شد.")


@admin.register(Course)
class CourseAdmin(admin.ModelAdmin):
    change_list_template = "admin/courses/course/change_list.html"
    list_display = (
        "title", "course_type", "price_display", "start_date", "instructor",
        "registration_open", "is_active", "locked", "last_crawled_at",
    )
    list_filter = ("course_type", "registration_open", "is_active", "locked")
    list_editable = ("registration_open", "is_active")
    search_fields = ("title", "instructor", "summary", "description")
    readonly_fields = ("source_id", "last_crawled_at", "created_at", "updated_at")
    actions = [unlock, activate, deactivate]
    fieldsets = (
        (None, {"fields": ("title", "course_type", "categories", "instructor", "instructor_bio")}),
        ("زمان‌بندی و قیمت", {"fields": (
            "duration", "price_rial", "price_text", "start_date", "schedule", "class_days", "registration_open",
        )}),
        ("محتوا", {"fields": ("summary", "description", "extra_notes", "url", "image_url")}),
        ("همگام‌سازی", {"fields": ("is_active", "locked", "source_id", "last_crawled_at", "created_at", "updated_at")}),
    )

    @admin.display(description="قیمت")
    def price_display(self, obj):
        return obj.price_display

    def save_model(self, request, obj, form, change):
        # Protect manual edits of crawled data from being overwritten by the next crawl,
        # unless the admin explicitly toggled the lock in the same edit.
        if change and "locked" not in form.changed_data and set(form.changed_data) & set(CRAWLED_FIELDS):
            obj.locked = True
            messages.info(request, "این دوره قفل شد: خزنده تغییرات شما را بازنویسی نخواهد کرد.")
        super().save_model(request, obj, form, change)

    def get_urls(self):
        custom = [path("crawl/", self.admin_site.admin_view(self.crawl_view), name="courses_course_crawl")]
        return custom + super().get_urls()

    def crawl_view(self, request):
        if request.method != "POST" or not self.has_change_permission(request):
            return redirect(reverse("admin:courses_course_changelist"))
        try:
            result = crawl_site()
            stats = save_crawl(result)
        except Exception as exc:  # noqa: BLE001 - show any crawl failure to the admin
            self.message_user(request, f"خزش سایت ناموفق بود: {exc}", messages.ERROR)
        else:
            self.message_user(request, (
                f"خزش سایت تمام شد: {stats['created']} دوره جدید، {stats['updated']} به‌روزرسانی، "
                f"{stats['skipped_locked']} قفل‌شده (رد شد)، {stats['deactivated']} غیرفعال شد."
            ))
            for error in result.errors:
                self.message_user(request, error, messages.WARNING)
        return redirect(reverse("admin:courses_course_changelist"))


@admin.register(KnowledgeEntry)
class KnowledgeEntryAdmin(admin.ModelAdmin):
    list_display = ("title", "source", "is_active", "locked", "updated_at")
    list_filter = ("source", "is_active", "locked")
    list_editable = ("is_active",)
    search_fields = ("title", "content")
    readonly_fields = ("updated_at",)
    actions = [unlock, activate, deactivate]

    def save_model(self, request, obj, form, change):
        if change and obj.key and "locked" not in form.changed_data and {"title", "content"} & set(form.changed_data):
            obj.locked = True
        super().save_model(request, obj, form, change)
