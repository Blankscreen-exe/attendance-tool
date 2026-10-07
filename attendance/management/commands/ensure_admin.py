import os

from django.core.management.base import BaseCommand

from attendance.models import Employee


class Command(BaseCommand):
    help = "Creates the first admin from ADMIN_USERNAME and ADMIN_PASSWORD if no admin exists yet."

    def handle(self, *args, **options):
        if Employee.objects.filter(is_superuser=True).exists():
            return
        username = os.environ.get("ADMIN_USERNAME")
        password = os.environ.get("ADMIN_PASSWORD")
        if not username or not password:
            self.stdout.write(
                "No admin account yet. Set ADMIN_USERNAME and ADMIN_PASSWORD, "
                "or run: python manage.py createsuperuser"
            )
            return
        Employee.objects.create_superuser(username=username, password=password)
        self.stdout.write(self.style.SUCCESS(f"Created admin account '{username}'."))
