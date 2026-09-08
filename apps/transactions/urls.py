from django.urls import path

from apps.transactions.views import TransactionAttemptViewSet, TransactionViewSet

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
transaction_cancel = TransactionViewSet.as_view(
    {
        "post": "cancel",
    }
)
transaction_attempt_list = TransactionAttemptViewSet.as_view({"get": "list"})
transaction_attempt_detail = TransactionAttemptViewSet.as_view({"get": "retrieve"})
transaction_attempt_dismiss = TransactionAttemptViewSet.as_view({"post": "dismiss"})

urlpatterns = [
    path(
        "clubs/<slug:club_slug>/transaction-attempts/",
        transaction_attempt_list,
        name="club-transaction-attempt-list",
    ),
    path(
        "clubs/<slug:club_slug>/transaction-attempts/<int:pk>/",
        transaction_attempt_detail,
        name="club-transaction-attempt-detail",
    ),
    path(
        "clubs/<slug:club_slug>/transaction-attempts/<int:pk>/dismiss/",
        transaction_attempt_dismiss,
        name="club-transaction-attempt-dismiss",
    ),
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
    path(
        "clubs/<slug:club_slug>/transactions/<int:pk>/cancel/",
        transaction_cancel,
        name="club-transaction-cancel",
    ),
]
