from django.contrib import admin

from .models import QuickSupportConfig, QuickSupportSession

admin.site.register(QuickSupportConfig)
admin.site.register(QuickSupportSession)
