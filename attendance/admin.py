"""Django's built-in admin, kept as a raw-data escape hatch for superusers.

Day-to-day administration happens under /manage/. Changes made here skip
the request-and-approval trail, so use it only to repair mistakes.
"""

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import (
    Correction,
    Employee,
    Holiday,
    LeaveAllowance,
    LeaveRequest,
    LoginThrottle,
    MissingTimeRequest,
    Schedule,
    ScheduleAssignment,
    ScheduleVersion,
    TimeEntry,
)


class ScheduleAssignmentInline(admin.TabularInline):
    model = ScheduleAssignment
    extra = 0


@admin.register(Employee)
class EmployeeAdmin(UserAdmin):
    fieldsets = (*UserAdmin.fieldsets, ("Attendance", {"fields": ("start_date",)}))
    list_display = ("username", "first_name", "last_name", "is_staff", "is_active")
    inlines = [ScheduleAssignmentInline]


class ScheduleVersionInline(admin.TabularInline):
    model = ScheduleVersion
    extra = 0


@admin.register(Schedule)
class ScheduleAdmin(admin.ModelAdmin):
    inlines = [ScheduleVersionInline]


@admin.register(TimeEntry)
class TimeEntryAdmin(admin.ModelAdmin):
    list_display = ("employee", "clock_in", "clock_out", "missing_out", "source")
    list_filter = ("source", "missing_out", "employee")
    date_hierarchy = "clock_in"
    raw_id_fields = ("request",)


@admin.register(MissingTimeRequest)
class MissingTimeRequestAdmin(admin.ModelAdmin):
    list_display = ("employee", "clock_in", "clock_out", "status", "decided_by")
    list_filter = ("status", "employee")
    raw_id_fields = ("entry",)


@admin.register(LeaveRequest)
class LeaveRequestAdmin(admin.ModelAdmin):
    list_display = ("employee", "leave_type", "start_date", "end_date", "status", "decided_by")
    list_filter = ("status", "leave_type", "employee")


@admin.register(Correction)
class CorrectionAdmin(admin.ModelAdmin):
    list_display = ("employee", "action", "admin", "created_at")
    list_filter = ("action", "employee")
    raw_id_fields = ("entry",)


admin.site.register(Holiday)
admin.site.register(LeaveAllowance)
admin.site.register(LoginThrottle)
