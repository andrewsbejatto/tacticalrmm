from django.urls import path

from . import views

urlpatterns = [
    # technician / app
    path("settings/", views.Settings.as_view()),
    path("server/", views.Server.as_view()),
    path("devices/", views.Devices.as_view()),
    path("devices/<int:pk>/connect/", views.DeviceConnect.as_view()),
    path("devices/<int:pk>/", views.DeviceDelete.as_view()),
    path("agents/<agent:agent_id>/", views.AgentDevice.as_view()),
    path("agents/<agent:agent_id>/provision/", views.Provision.as_view()),
    path("agents/<agent:agent_id>/password/", views.ChangePassword.as_view()),
    path("agents/<agent:agent_id>/connect/", views.AgentConnect.as_view()),
    path("bulk/provision/", views.BulkProvision.as_view()),
    path("quicksupport/download/", views.QuickSupportDownload.as_view()),
    path("installer/", views.CombinedInstaller.as_view()),
    # managed computers
    path("enroll/", views.Enroll.as_view()),
    path("download/<str:plat>/<str:arch>/", views.Download.as_view()),
]
