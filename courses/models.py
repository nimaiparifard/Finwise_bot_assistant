from django.db import models


class Course(models.Model):
    """A course offered by the center, crawled from the website and editable in the admin."""

    class CourseType(models.TextChoices):
        ONLINE = "online", "آنلاین (زنده)"
        OFFLINE = "offline", "آفلاین (ضبط‌شده)"
        OTHER = "other", "سایر"

    # Identity on the source website (course.cfm?id=<source_id>). Empty for courses added by hand.
    source_id = models.PositiveIntegerField("شناسه در سایت", null=True, blank=True, unique=True)
    url = models.URLField("لینک دوره", blank=True)

    title = models.CharField("عنوان", max_length=300)
    course_type = models.CharField("نوع دوره", max_length=20, choices=CourseType.choices, default=CourseType.ONLINE)
    categories = models.JSONField(
        "دسته‌بندی‌ها", default=list, blank=True, help_text='فهرست نام دسته‌ها، مثلاً ["مالیات"].'
    )
    instructor = models.CharField("مدرس", max_length=200, blank=True)
    instructor_bio = models.TextField("درباره مدرس", blank=True)
    duration = models.CharField("مدت دوره", max_length=100, blank=True, help_text='همان‌طور که در سایت آمده، مثلاً «۱۵ ساعت».')
    price_rial = models.PositiveBigIntegerField("قیمت (ریال)", null=True, blank=True, help_text="قیمت به ریال (۰ = رایگان).")
    price_text = models.CharField("قیمت (متن نمایشی)", max_length=200, blank=True, help_text="قیمت همان‌طور که در سایت نمایش داده می‌شود.")
    start_date = models.CharField("تاریخ شروع", max_length=50, blank=True, help_text="تاریخ شمسی، مثلاً ۱۴۰۵/۰۷/۲۷.")
    schedule = models.CharField("بازه برگزاری", max_length=200, blank=True, help_text="مثلاً «۲۷ مهر تا ۲۹ آبان».")
    class_days = models.CharField("روزهای برگزاری", max_length=200, blank=True, help_text="روز(های) هفته‌ای که کلاس برگزار می‌شود.")
    summary = models.TextField("خلاصه", blank=True)
    description = models.TextField("توضیحات", blank=True)
    image_url = models.URLField("لینک تصویر", blank=True)
    extra_notes = models.TextField(
        "نکات تکمیلی", blank=True,
        help_text="هر نکته دیگری که ربات باید درباره این دوره بداند (فقط توسط مدیر نوشته می‌شود و از سایت خوانده نمی‌شود).",
    )

    registration_open = models.BooleanField("ثبت‌نام باز است", default=False)
    is_active = models.BooleanField("فعال", default=True, help_text="دوره‌های غیرفعال برای ربات نمایش داده نمی‌شوند.")
    locked = models.BooleanField(
        "قفل شده",
        default=False,
        help_text="در صورت فعال بودن، خزنده این دوره را بازنویسی نمی‌کند. "
        "با ویرایش دوره در پنل مدیریت به‌طور خودکار فعال می‌شود.",
    )

    last_crawled_at = models.DateTimeField("آخرین خزش", null=True, blank=True)
    created_at = models.DateTimeField("تاریخ ایجاد", auto_now_add=True)
    updated_at = models.DateTimeField("آخرین ویرایش", auto_now=True)

    class Meta:
        ordering = ["-registration_open", "title"]
        verbose_name = "دوره"
        verbose_name_plural = "دوره‌ها"

    def __str__(self):
        return self.title

    @property
    def price_display(self):
        if self.price_rial is None:
            return self.price_text or "نامشخص"
        if self.price_rial == 0:
            return "رایگان"
        return f"{self.price_rial:,} ریال ({self.price_rial // 10:,} تومان)"

    @property
    def price_toman_display(self):
        """Toman only: what the bot sees, so the model can never mistake a Rial figure for Toman."""
        if self.price_rial is None:
            return self.price_text or "نامشخص"
        if self.price_rial == 0:
            return "رایگان"
        return f"{self.price_rial // 10:,} تومان"


class KnowledgeEntry(models.Model):
    """General information the bot may use: center info, rules, payment, FAQ answers, etc."""

    class Source(models.TextChoices):
        CRAWLED = "crawled", "خزیده‌شده از سایت"
        ADMIN = "admin", "نوشته‌شده توسط مدیر"
        FAQ = "faq", "ذخیره‌شده از سؤال پاسخ‌داده‌شده"

    key = models.SlugField(
        "کلید", max_length=100, unique=True, null=True, blank=True,
        help_text="شناسه ثابت برای خزنده. برای موارد دستی خالی بگذارید.",
    )
    title = models.CharField("عنوان", max_length=300)
    content = models.TextField("متن")
    source = models.CharField("منبع", max_length=20, choices=Source.choices, default=Source.ADMIN)
    is_active = models.BooleanField("فعال", default=True)
    locked = models.BooleanField("قفل شده", default=False, help_text="در صورت فعال بودن، خزنده این مورد را بازنویسی نمی‌کند.")
    updated_at = models.DateTimeField("آخرین ویرایش", auto_now=True)

    class Meta:
        ordering = ["source", "title"]
        verbose_name = "اطلاعات و پرسش متداول"
        verbose_name_plural = "اطلاعات و پرسش‌های متداول"

    def __str__(self):
        return self.title
