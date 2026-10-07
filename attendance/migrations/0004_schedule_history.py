import django.core.validators
import django.db.models.deletion
from datetime import date
from decimal import Decimal
from django.conf import settings
from django.db import migrations, models

WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
BEGINNING = date(2000, 1, 3)  # same Monday as attendance.dates.BEGINNING


def copy_into_history(apps, schema_editor):
    """Keeps every schedule's hours and every employee's schedule as the first entry in their history."""
    Schedule = apps.get_model("attendance", "Schedule")
    ScheduleVersion = apps.get_model("attendance", "ScheduleVersion")
    ScheduleAssignment = apps.get_model("attendance", "ScheduleAssignment")
    Employee = apps.get_model("attendance", "Employee")

    for schedule in Schedule.objects.all():
        ScheduleVersion.objects.create(
            schedule=schedule,
            effective_from=BEGINNING,
            weekly_hours=schedule.weekly_hours,
            **{day: getattr(schedule, day) for day in WEEKDAYS},
        )
    for employee in Employee.objects.exclude(schedule=None):
        ScheduleAssignment.objects.create(
            employee=employee, schedule_id=employee.schedule_id, effective_from=BEGINNING
        )


class Migration(migrations.Migration):

    dependencies = [
        ('attendance', '0003_login_throttle'),
    ]

    operations = [
        migrations.CreateModel(
            name='ScheduleAssignment',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('effective_from', models.DateField()),
                ('employee', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='assignments', to=settings.AUTH_USER_MODEL)),
                ('schedule', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='assignments', to='attendance.schedule')),
            ],
            options={
                'ordering': ['effective_from'],
                'constraints': [models.UniqueConstraint(fields=('employee', 'effective_from'), name='one_assignment_per_employee_week')],
            },
        ),
        migrations.CreateModel(
            name='ScheduleVersion',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('effective_from', models.DateField()),
                ('weekly_hours', models.DecimalField(decimal_places=2, help_text='Hours to complete each week. Holidays and approved leave reduce it pro rata.', max_digits=5, validators=[django.core.validators.MinValueValidator(Decimal('0.5')), django.core.validators.MaxValueValidator(Decimal('112'))])),
                ('monday', models.BooleanField(default=True)),
                ('tuesday', models.BooleanField(default=True)),
                ('wednesday', models.BooleanField(default=True)),
                ('thursday', models.BooleanField(default=True)),
                ('friday', models.BooleanField(default=True)),
                ('saturday', models.BooleanField(default=False)),
                ('sunday', models.BooleanField(default=False)),
                ('schedule', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='versions', to='attendance.schedule')),
            ],
            options={
                'ordering': ['effective_from'],
                'constraints': [models.UniqueConstraint(fields=('schedule', 'effective_from'), name='one_version_per_schedule_week')],
            },
        ),
        # Copy the existing data across before the old columns go.
        migrations.RunPython(copy_into_history, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name='employee',
            name='schedule',
        ),
        migrations.RemoveField(
            model_name='schedule',
            name='friday',
        ),
        migrations.RemoveField(
            model_name='schedule',
            name='monday',
        ),
        migrations.RemoveField(
            model_name='schedule',
            name='saturday',
        ),
        migrations.RemoveField(
            model_name='schedule',
            name='sunday',
        ),
        migrations.RemoveField(
            model_name='schedule',
            name='thursday',
        ),
        migrations.RemoveField(
            model_name='schedule',
            name='tuesday',
        ),
        migrations.RemoveField(
            model_name='schedule',
            name='wednesday',
        ),
        migrations.RemoveField(
            model_name='schedule',
            name='weekly_hours',
        ),
    ]
