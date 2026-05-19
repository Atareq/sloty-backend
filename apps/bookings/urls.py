from django.urls import path

from apps.bookings.views import BookingViewSet

booking_list = BookingViewSet.as_view(
    {
        "get": "list",
        "post": "create",
    }
)
booking_detail = BookingViewSet.as_view(
    {
        "get": "retrieve",
        "patch": "partial_update",
    }
)

urlpatterns = [
    path("clubs/<slug:club_slug>/bookings/", booking_list, name="club-booking-list"),
    path(
        "clubs/<slug:club_slug>/bookings/<int:pk>/",
        booking_detail,
        name="club-booking-detail",
    ),
]
