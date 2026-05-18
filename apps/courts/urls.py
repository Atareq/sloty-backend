from rest_framework.routers import DefaultRouter

from apps.courts.views import (
    CourtStaffAssignmentViewSet,
    CourtViewSet,
    CourtWorkingHourViewSet,
)

router = DefaultRouter()
router.register("courts", CourtViewSet, basename="court")
router.register(
    "court-working-hours",
    CourtWorkingHourViewSet,
    basename="court-working-hour",
)
router.register(
    "court-staff-assignments",
    CourtStaffAssignmentViewSet,
    basename="court-staff-assignment",
)

urlpatterns = router.urls
