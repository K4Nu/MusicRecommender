from django.shortcuts import render
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework import permissions, status
from recomendations.models import ColdStartTrack
from recomendations.serializers import ColdStartTrackSerializer, OnboardingEventSerializer
from users.models import SpotifyAccount, YoutubeAccount
from django.utils import timezone
from .tasks.cold_start_tasks import create_cold_start_lastfm_tracks
from .services.cold_start import cold_start_refresh_all
from django.db import IntegrityError, transaction
from recomendations.models import OnboardingEvent
import logging
from django.db.models import Count, Q
from django.views.decorators.cache import cache_page
from django.utils.decorators import method_decorator
from celery import chain
logger = logging.getLogger(__name__)


class ColdTest(APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        cold_start_refresh_all.delay()
        create_cold_start_lastfm_tracks()

        return Response(
            {"message": "Cold start"},
            status=status.HTTP_200_OK
        )


"""
By now without postgres
"""


from django.db.models import Count, Q
from django.utils import timezone
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import permissions
import logging

from users.models import SpotifyAccount, YoutubeAccount
from recomendations.models import ColdStartTrack, OnboardingEvent
from recomendations.serializers import ColdStartTrackSerializer

logger = logging.getLogger(__name__)


class InitialSetupView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    REQUIRED_LIKES = 3
    TRACKS_PER_BATCH = 20

    def get(self, request):
        user = request.user
        profile = user.profile

        has_spotify = SpotifyAccount.objects.filter(user=user).exists()
        has_youtube = YoutubeAccount.objects.filter(user=user).exists()
        has_any_integration = has_spotify or has_youtube

        needs_onboarding = not profile.onboarding_completed
        needs_integration = not has_any_integration

        stats = OnboardingEvent.objects.filter(user=user).aggregate(
            likes_count=Count(
                "id",
                filter=Q(action=OnboardingEvent.Action.LIKE),
            )
        )

        likes = stats["likes_count"] or 0

        response = {
            "needs_onboarding": needs_onboarding,
            "needs_integration": needs_integration,
            "tracks": [],
            "stats": {
                "likes_count": likes,
                "required_likes": self.REQUIRED_LIKES,
            },
        }

        if not needs_onboarding:
            logger.info(f"InitialSetupView response: {response}")
            return Response(response)

        # 🎧 REAL ONBOARDING (tylko jeśli NIE ma integracji)
        if needs_integration:
            if not profile.onboarding_started_at:
                profile.onboarding_started_at = timezone.now()
                profile.save(update_fields=["onboarding_started_at"])

            tracks = self._get_coldstart_tracks(limit=self.TRACKS_PER_BATCH)
            response["tracks"] = ColdStartTrackSerializer(tracks, many=True).data

        logger.info(f"InitialSetupView response: {response}")
        return Response(response)

    def _get_coldstart_tracks(self, limit: int):
        """
        Returns a batch of cold start tracks with artist diversity.
        """
        qs = (
            ColdStartTrack.objects
            .filter(track__spotify_id__isnull=False)
            .select_related("track")
            .prefetch_related("track__artists")
            .order_by("?")[:50]
        )

        selected = []
        seen_artists = set()

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

        # fallback
        if len(selected) < limit:
            selected = list(qs[:limit])

        return selected


class GetFeature(APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        features = create_cold_start_lastfm_tracks()

        return Response(
            {"message": "Cold start"},
            status=status.HTTP_200_OK
        )


class OnboardingInteractView(APIView):
    """
    Handle batch onboarding event submissions.
    Endless until user reaches MIN_LIKES_TO_COMPLETE.
    """
    permission_classes = [permissions.IsAuthenticated]

    MAX_EVENTS_PER_BATCH = 20
    MIN_LIKES_TO_COMPLETE = 3

    def post(self, request):
        user = request.user
        profile = user.profile

        if profile.onboarding_completed:
            return Response(
                {
                    "status": "already_completed",
                    "quality": profile.onboarding_quality,
                },
                status=status.HTTP_200_OK,
            )

        events_data = request.data.get("events", [])

        if not events_data:
            return Response(
                {"error": "No events provided"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if len(events_data) > self.MAX_EVENTS_PER_BATCH:
            return Response(
                {"error": "Too many events in one request"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = OnboardingEventSerializer(
            data=events_data,
            many=True,
        )
        serializer.is_valid(raise_exception=True)

        # mark onboarding start once
        if not profile.onboarding_started_at:
            profile.onboarding_started_at = timezone.now()
            profile.save(update_fields=["onboarding_started_at"])

        validated_events = serializer.validated_data

        track_ids = {e["cold_start_track_id"] for e in validated_events}
        cold_start_tracks = {
            t.id: t
            for t in ColdStartTrack.objects.filter(id__in=track_ids)
        }

        events_written = 0
        events_ignored = 0

        with transaction.atomic():
            for event_data in validated_events:
                cold_start_track = cold_start_tracks.get(
                    event_data["cold_start_track_id"]
                )

                if not cold_start_track:
                    logger.warning(
                        "ColdStartTrack %s not found",
                        event_data["cold_start_track_id"],
                    )
                    continue

                event, created = OnboardingEvent.objects.get_or_create(
                    user=user,
                    cold_start_track=cold_start_track,
                    defaults={
                        "action": event_data["action"],
                        "position": event_data.get("position"),
                    },
                )

                if created:
                    events_written += 1
                    continue

                # update only if changed
                updated_fields = []

                if event.action != event_data["action"]:
                    event.action = event_data["action"]
                    updated_fields.append("action")

                if (
                        event_data.get("position") is not None
                        and event.position != event_data["position"]
                ):
                    event.position = event_data["position"]
                    updated_fields.append("position")

                if updated_fields:
                    event.save(update_fields=updated_fields)
                    events_written += 1
                else:
                    events_ignored += 1

        stats = OnboardingEvent.objects.filter(user=user).aggregate(
            total_count=Count("id"),
            likes_count=Count(
                "id",
                filter=Q(action=OnboardingEvent.Action.LIKE),
            ),
        )

        likes = stats["likes_count"] or 0

        if likes >= self.MIN_LIKES_TO_COMPLETE:
            profile.onboarding_completed = True
            profile.onboarding_completed_at = timezone.now()
            profile.onboarding_quality = "GOOD"

            profile.save(update_fields=[
                "onboarding_completed",
                "onboarding_completed_at",
                "onboarding_quality",
            ])

            return Response(
                {
                    "status": "onboarding_completed",
                    "quality": profile.onboarding_quality,
                    "events_written": events_written,
                    "events_ignored": events_ignored,
                    "stats": stats,
                },
                status=status.HTTP_200_OK,
            )

        # 🔁 ENDLESS MODE
        return Response(
            {
                "status": "needs_more_likes",
                "events_written": events_written,
                "events_ignored": events_ignored,
                "stats": stats,
                "likes_missing": self.MIN_LIKES_TO_COMPLETE - likes,
                "onboarding_completed": False,
            },
            status=status.HTTP_200_OK,
        )
@method_decorator(cache_page(10), name="get")
class UserStatus(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        user=request.user

        return Response({
            "onboarding_completed": user.profile.onboarding_completed,
            "has_spotify": SpotifyAccount.objects.filter(user=user).exists(),
            "has_youtube": YoutubeAccount.objects.filter(user=user).exists(),
        })

