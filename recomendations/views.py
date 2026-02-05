from django.shortcuts import render
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework import permissions, status
from .services.cold_start import cold_start_fetch_spotify_global
from recomendations.models import ColdStartTrack
from recomendations.serializers import ColdStartTrackSerializer
from users.models import SpotifyAccount,YoutubeAccount
from django.utils import timezone
from .tasks.cold_start_tasks import create_cold_start_lastfm_tracks
from django.db import IntegrityError, transaction
from recomendations.models import OnboardingEvent
from recommndations.serializers import OnboardingEventSerializer

class ColdTest(APIView):
    permission_classes = [permissions.AllowAny]

    def get(self,request):
        cold_start_fetch_spotify_global.delay()
        return Response(
            {"message": "Cold start"},
            status=status.HTTP_200_OK
        )
"""
By now without postgres
"""
class InitialSetupView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        user = request.user
        profile = user.profile

        has_spotify = SpotifyAccount.objects.filter(user=user).exists()
        has_youtube = YoutubeAccount.objects.filter(user=user).exists()

        needs_integration = not (has_spotify or has_youtube)
        needs_onboarding = not profile.onboarding_completed

        response = {
            "has_spotify": has_spotify,
            "has_youtube": has_youtube,
            "needs_integration": needs_integration,
            "needs_onboarding": needs_onboarding,
            "needs_setup": needs_integration or needs_onboarding,
            "tracks": None,
        }

        if needs_onboarding:
            if not profile.onboarding_started_at:
                profile.onboarding_started_at = timezone.now()
                profile.save(update_fields=["onboarding_started_at"])

            tracks = self._get_coldstart_tracks()
            response["tracks"] = ColdStartTrackSerializer(tracks, many=True).data

        return Response(response)

    def _get_coldstart_tracks(self):
        limit = 7

        qs = (
            ColdStartTrack.objects
            .filter(track__spotify_id__isnull=False)
            .select_related("track")
            .prefetch_related("track__artists")
            .order_by("?")[:50]
        )

        seen_artists = set()
        selected = []

        for cst in qs:
            artists = list(cst.track.artists.all())
            if not artists:
                continue

            main_artist_id = artists[0].id
            if main_artist_id in seen_artists:
                continue

            seen_artists.add(main_artist_id)
            selected.append(cst)

            if len(selected) >= limit:
                break

        return selected

class GetFeature(APIView):
    permission_classes = [permissions.AllowAny]

    def get(self,request):
        from users.tasks.spotify_tasks import fetch_tracks_audio_features
        features = create_cold_start_lastfm_tracks()

        return Response(
            {"message": "Cold start"},
            status=status.HTTP_200_OK
        )

