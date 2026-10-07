from django.db import migrations, models

# A common arrangement in Pakistan, as a starting point. Admins change these on the Leave page.
STARTING_ALLOWANCES = {"annual": 14, "casual": 10, "sick": 8}


def add_starting_allowances(apps, schema_editor):
    LeaveAllowance = apps.get_model("attendance", "LeaveAllowance")
    for leave_type, days in STARTING_ALLOWANCES.items():
        LeaveAllowance.objects.get_or_create(leave_type=leave_type, defaults={"days": days})


class Migration(migrations.Migration):

    dependencies = [
        ('attendance', '0005_corrections'),
    ]

    operations = [
        migrations.CreateModel(
            name='LeaveAllowance',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('leave_type', models.CharField(choices=[('annual', 'Annual'), ('casual', 'Casual'), ('sick', 'Sick'), ('unpaid', 'Unpaid'), ('other', 'Other')], max_length=10, unique=True)),
                ('days', models.PositiveSmallIntegerField()),
            ],
        ),
        migrations.RunPython(add_starting_allowances, migrations.RunPython.noop),
    ]
