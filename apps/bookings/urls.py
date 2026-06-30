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
booking_cancel = BookingViewSet.as_view({"post": "cancel"})
booking_complete = BookingViewSet.as_view({"post": "complete"})
booking_no_show = BookingViewSet.as_view({"post": "no_show"})
booking_expire = BookingViewSet.as_view({"post": "expire"})

urlpatterns = [
    path("clubs/<slug:club_slug>/bookings/", booking_list, name="club-booking-list"),
    path(
        "clubs/<slug:club_slug>/bookings/<int:pk>/",
        booking_detail,
        name="club-booking-detail",
    ),
    path(
        "clubs/<slug:club_slug>/bookings/<int:pk>/cancel/",
        booking_cancel,
        name="club-booking-cancel",
    ),
    path(
        "clubs/<slug:club_slug>/bookings/<int:pk>/complete/",
        booking_complete,
        name="club-booking-complete",
    ),
    path(
        "clubs/<slug:club_slug>/bookings/<int:pk>/no-show/",
        booking_no_show,
        name="club-booking-no-show",
    ),
    path(
        "clubs/<slug:club_slug>/bookings/<int:pk>/expire/",
        booking_expire,
        name="club-booking-expire",
    ),
]
