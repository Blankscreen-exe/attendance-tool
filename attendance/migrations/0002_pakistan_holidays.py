from django.db import migrations
from django.utils import timezone

from attendance import pk_holidays


def add_holidays(apps, schema_editor):
    """Starts the holiday list with Pakistan's public holidays.

    Runs once, so holidays an admin later edits or removes stay that way.
    """
    Holiday = apps.get_model("attendance", "Holiday")
    pk_holidays.add_fixed_holidays(Holiday)
    year = timezone.localdate().year
    for lunar_year in (year, year + 1):
        pk_holidays.add_lunar_holidays(Holiday, lunar_year)


class Migration(migrations.Migration):
    dependencies = [("attendance", "0001_initial")]

    operations = [migrations.RunPython(add_holidays, migrations.RunPython.noop)]
