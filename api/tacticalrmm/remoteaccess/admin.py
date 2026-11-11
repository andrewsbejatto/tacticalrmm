from django.contrib import admin

from .models import RemoteAccessConfig, RemoteAccessDevice

admin.site.register(RemoteAccessConfig)
admin.site.register(RemoteAccessDevice)
