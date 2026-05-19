from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("clubs", "0002_club_scoped_memberships"),
        ("courts", "0001_initial"),
    ]

    operations = [
        migrations.DeleteModel(
            name="CourtStaffAssignment",
        ),
    ]
