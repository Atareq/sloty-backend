from django.urls import path

from apps.settlements.views import SettlementViewSet

settlement_list = SettlementViewSet.as_view(
    {
        "get": "list",
        "post": "create",
    }
)
settlement_detail = SettlementViewSet.as_view({"get": "retrieve"})
settlement_preview = SettlementViewSet.as_view({"get": "preview"})
settlement_unsettled_summary = SettlementViewSet.as_view({"get": "unsettled_summary"})
settlement_mark_settled = SettlementViewSet.as_view({"post": "mark_settled"})

urlpatterns = [
    path(
        "clubs/<slug:club_slug>/settlements/",
        settlement_list,
        name="club-settlement-list",
    ),
    path(
        "clubs/<slug:club_slug>/settlements/preview/",
        settlement_preview,
        name="club-settlement-preview",
    ),
    path(
        "clubs/<slug:club_slug>/settlements/unsettled-summary/",
        settlement_unsettled_summary,
        name="club-settlement-unsettled-summary",
    ),
    path(
        "clubs/<slug:club_slug>/settlements/<int:pk>/",
        settlement_detail,
        name="club-settlement-detail",
    ),
    path(
        "clubs/<slug:club_slug>/settlements/<int:pk>/mark-settled/",
        settlement_mark_settled,
        name="club-settlement-mark-settled",
    ),
]
