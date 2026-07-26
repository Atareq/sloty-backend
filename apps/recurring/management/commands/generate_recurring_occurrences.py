from django.core.management.base import BaseCommand

from apps.recurring.services import maintain_all_rolling_horizons


class Command(BaseCommand):
    help = "Maintain the rolling occurrence horizon for active recurring agreements."

    def handle(self, *args, **options):
        result = maintain_all_rolling_horizons()
        self.stdout.write(
            self.style.SUCCESS(
                "Maintained={maintained} failures={failures}".format(**result)
            )
        )
