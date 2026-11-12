from django.urls import path

from . import views

urlpatterns = [
    # dashboard / app
    path("", views.Recordings.as_view()),
    path("<int:pk>/play/", views.Play.as_view()),
    path("settings/", views.Settings.as_view()),
    path("settings/test/", views.SettingsTest.as_view()),
    # computers that record (TRMM Desktop host / quick support)
    path("upload/", views.Upload.as_view()),
    path("qs/<uuid:pk>/token/", views.QuickSupportToken.as_view()),
]
