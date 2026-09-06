from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("bookings", "0003_recurring_agreements"),
    ]

    operations = [
        migrations.AddField(
            model_name="booking",
            name="client_request_id",
            field=models.UUIDField(blank=True, db_index=True, null=True),
        ),
        migrations.AddConstraint(
            model_name="booking",
            constraint=models.UniqueConstraint(
                condition=models.Q(("client_request_id__isnull", False)),
                fields=("club", "client_request_id"),
                name="booking_client_request_once_per_club",
            ),
        ),
    ]
