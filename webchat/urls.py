from django.urls import path

from . import views

urlpatterns = [
    path("", views.index, name="chat"),
    path("api/chat/", views.chat, name="chat-api"),
]
