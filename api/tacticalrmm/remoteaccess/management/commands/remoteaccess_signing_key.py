"""
    python manage.py remoteaccess_signing_key

Prints the public key that signs the config embedded in TRMM Desktop
executables (quick support). Put it in D:\\TRMMDesktop\\trailer-keys.txt (one
key per line) and rebuild TRMM Desktop: the app only accepts configs signed
by the keys of its build.
"""

from django.core.management.base import BaseCommand

from remoteaccess.crypto import signing_public_key


class Command(BaseCommand):
    help = "Public key of the TRMM Desktop embedded config signature"

    def handle(self, *args, **kwargs):
        self.stdout.write(signing_public_key())
