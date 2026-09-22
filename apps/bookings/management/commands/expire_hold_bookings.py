from django.core.management.base import BaseCommand

from apps.bookings.services import expire_due_hold_bookings


class Command(BaseCommand):
    help = (
        "Persist EXPIRED for HOLD bookings whose stored hold_expires_at "
        "deadline has passed. "
        "Overlap and availability already treat those HOLDs as free in real "
        "time. This command only cleans persisted rows. Production runs it "
        "from sloty-hold-expiry.timer every 5 minutes; the timer is not "
        "required for slot availability correctness."
    )

    def handle(self, *args, **options):
        expired_bookings = expire_due_hold_bookings()
        count = len(expired_bookings)
        self.stdout.write(self.style.SUCCESS(f"Expired {count} hold booking(s)."))
