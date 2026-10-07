from django.apps import AppConfig


class AttendanceConfig(AppConfig):
    name = "attendance"

    def ready(self):
        from . import lockout  # noqa: F401  (connects the sign-in signals)
