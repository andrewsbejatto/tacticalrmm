from django.urls import path

from . import views

urlpatterns = [
    # used by the quick support executable
    path("client/sessions/", views.ClientSessions.as_view()),
    path(
        "client/sessions/<uuid:pk>/heartbeat/",
        views.ClientSessionHeartbeat.as_view(),
    ),
    path("client/sessions/<uuid:pk>/close/", views.ClientSessionClose.as_view()),
    path(
        "client/sessions/<uuid:pk>/install/request/",
        views.ClientInstallRequest.as_view(),
    ),
    path("client/sessions/<uuid:pk>/installer/", views.ClientInstaller.as_view()),
    path(
        "client/sessions/<uuid:pk>/install/result/",
        views.ClientInstallResult.as_view(),
    ),
    path("client/meshagent/", views.ClientMeshAgent.as_view()),
    # dashboard
    path("sessions/", views.Sessions.as_view()),
    path("sessions/<uuid:pk>/end/", views.EndSession.as_view()),
    path("sessions/<uuid:pk>/install/", views.ApproveInstall.as_view()),
    path("connect/", views.Connect.as_view()),
    path("settings/", views.Settings.as_view()),
    path("settings/resettoken/", views.ResetClientToken.as_view()),
    path("download/", views.DownloadClient.as_view()),
]
