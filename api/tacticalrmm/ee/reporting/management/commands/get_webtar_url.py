"""
Copyright (c) 2023-present Amidaware Inc.
This file is subject to the EE License Agreement.
For details, see: https://license.tacticalrmm.com/ee
"""

import urllib.parse
from typing import Any, Optional

from core.models import CodeSignToken
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Get webtar url"

    def handle(self, *args: tuple[Any, Any], **kwargs: dict[str, Any]) -> None:
        # self built frontend (e.g. with translations), set in local_settings.py
        custom_url = getattr(settings, "CUSTOM_WEBTAR_URL", None)
        if custom_url:
            # the downloaded files become the dashboard: never over plain http
            if not custom_url.lower().startswith("https://"):
                raise CommandError("CUSTOM_WEBTAR_URL must use https://")
            self.stdout.write(custom_url)
            return

        webtar = f"trmm-web-v{settings.WEB_VERSION}.tar.gz"
        url = f"https://github.com/amidaware/tacticalrmm-web/releases/download/v{settings.WEB_VERSION}/{webtar}"

        t: "Optional[CodeSignToken]" = CodeSignToken.objects.first()
        if not t or not t.token:
            self.stdout.write(url)
            return

        if t.is_valid:
            params = {
                "token": t.token,
                "webver": settings.WEB_VERSION,
                "api": settings.ALLOWED_HOSTS[0],
            }
            url = settings.WEBTAR_DL_URL + urllib.parse.urlencode(params)

        self.stdout.write(url)
