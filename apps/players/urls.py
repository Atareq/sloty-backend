from django.urls import path

from apps.players.views import ClubPlayerViewSet, PlayerProfileViewSet

# ClubPlayer — club-scoped CRUD
club_player_list = ClubPlayerViewSet.as_view(
    {
        "get": "list",
        "post": "create",
    }
)
club_player_detail = ClubPlayerViewSet.as_view(
    {
        "get": "retrieve",
    }
)

# PlayerProfile — club-membership-gated global profile access
player_profile_list = PlayerProfileViewSet.as_view(
    {
        "get": "list",
        "post": "create",
    }
)
player_profile_detail = PlayerProfileViewSet.as_view(
    {
        "get": "retrieve",
    }
)

urlpatterns = [
    # Club-scoped player roster
    path(
        "clubs/<slug:club_slug>/players/",
        club_player_list,
        name="club-player-list",
    ),
    path(
        "clubs/<slug:club_slug>/players/<int:pk>/",
        club_player_detail,
        name="club-player-detail",
    ),
    # Global player profiles (club-membership-gated; list is club-linked only)
    path(
        "clubs/<slug:club_slug>/player-profiles/",
        player_profile_list,
        name="club-player-profile-list",
    ),
    path(
        "clubs/<slug:club_slug>/player-profiles/<int:pk>/",
        player_profile_detail,
        name="club-player-profile-detail",
    ),
]
