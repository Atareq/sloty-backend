from django.core.management.base import BaseCommand

from apps.bookings.services import expire_due_hold_bookings


class Command(BaseCommand):
    help = "Expire due HOLD bookings using each court's internal hold expiry hours."

    def handle(self, *args, **options):
        expired_bookings = expire_due_hold_bookings()
        count = len(expired_bookings)
        self.stdout.write(self.style.SUCCESS(f"Expired {count} hold booking(s)."))
