from django.urls import path

from apps.transactions.views import TransactionViewSet

transaction_list = TransactionViewSet.as_view(
    {
        "get": "list",
        "post": "create",
    }
)
transaction_detail = TransactionViewSet.as_view(
    {
        "get": "retrieve",
    }
)

urlpatterns = [
    path(
        "clubs/<slug:club_slug>/transactions/",
        transaction_list,
        name="club-transaction-list",
    ),
    path(
        "clubs/<slug:club_slug>/transactions/<int:pk>/",
        transaction_detail,
        name="club-transaction-detail",
    ),
]
