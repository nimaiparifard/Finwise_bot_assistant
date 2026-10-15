from django.contrib import admin
from django.urls import include, path

admin.site.site_header = "پنل مدیریت ربات فین‌وایز"
admin.site.site_title = "مدیریت فین‌وایز"
admin.site.index_title = "دوره‌ها، پایگاه دانش و سؤالات کاربران"

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("webchat.urls")),
]
