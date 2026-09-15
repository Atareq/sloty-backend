import django.db.models.deletion
from django.db import migrations, models


def delete_bookings_without_club_player(apps, schema_editor):
    Booking = apps.get_model("bookings", "Booking")
    BookingAttempt = apps.get_model("bookings", "BookingAttempt")
    orphaned_booking_ids = Booking.objects.filter(
        club_player_id__isnull=True
    ).values_list("id", flat=True)
    BookingAttempt.objects.filter(booking_id__in=orphaned_booking_ids).delete()
    Booking.objects.filter(club_player_id__isnull=True).delete()


class Migration(migrations.Migration):

    atomic = False

    dependencies = [
        ("bookings", "0008_booking_club_player_and_more"),
        ("players", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(
            delete_bookings_without_club_player,
            migrations.RunPython.noop,
        ),
        migrations.RemoveField(
            model_name="booking",
            name="customer_name",
        ),
        migrations.RemoveField(
            model_name="booking",
            name="customer_phone",
        ),
        migrations.AlterField(
            model_name="booking",
            name="club_player",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="bookings",
                to="players.clubplayer",
            ),
        ),
    ]
