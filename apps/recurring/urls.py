from django.urls import path

from apps.recurring.views import RecurringAgreementViewSet

agreement_list = RecurringAgreementViewSet.as_view(
    {
        "get": "list",
        "post": "create",
    }
)
agreement_availability = RecurringAgreementViewSet.as_view({"get": "availability"})
agreement_detail = RecurringAgreementViewSet.as_view({"get": "retrieve"})
agreement_cancellation_preview = RecurringAgreementViewSet.as_view(
    {"post": "cancellation_preview"}
)
agreement_cancel = RecurringAgreementViewSet.as_view({"post": "cancel"})
agreement_refund_deposit = RecurringAgreementViewSet.as_view({"post": "refund_deposit"})

urlpatterns = [
    path(
        "clubs/<slug:club_slug>/recurring-agreements/",
        agreement_list,
        name="club-recurring-agreement-list",
    ),
    path(
        "clubs/<slug:club_slug>/recurring-agreements/availability/",
        agreement_availability,
        name="club-recurring-agreement-availability",
    ),
    path(
        "clubs/<slug:club_slug>/recurring-agreements/<int:pk>/",
        agreement_detail,
        name="club-recurring-agreement-detail",
    ),
    path(
        "clubs/<slug:club_slug>/recurring-agreements/<int:pk>/cancellation-preview/",
        agreement_cancellation_preview,
        name="club-recurring-agreement-cancellation-preview",
    ),
    path(
        "clubs/<slug:club_slug>/recurring-agreements/<int:pk>/cancel/",
        agreement_cancel,
        name="club-recurring-agreement-cancel",
    ),
    path(
        "clubs/<slug:club_slug>/recurring-agreements/<int:pk>/refund-deposit/",
        agreement_refund_deposit,
        name="club-recurring-agreement-refund-deposit",
    ),
]
