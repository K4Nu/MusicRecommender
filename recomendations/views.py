from django.shortcuts import render
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework import permissions, status
from .services.cold_start import cold_start_fetch_spotify_global
from recomendations.models import ColdStartTrack
from recomendations.serializers import ColdStartTrackSerializer,OnboardingEventSerializer
from users.models import SpotifyAccount,YoutubeAccount
from django.utils import timezone
from .tasks.cold_start_tasks import create_cold_start_lastfm_tracks
from django.db import IntegrityError, transaction
from recomendations.models import OnboardingEvent
import logging
from django.db.models import Count, Q


logger = logging.getLogger(__name__)

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

        stats = OnboardingEvent.objects.filter(user=user).aggregate(
            total=Count("id"),
            likes=Count("id", filter=Q(action=OnboardingEvent.Action.LIKE)),
        )

        likes = stats["likes"] or 0

        onboarding_completed = likes >= 3
        needs_onboarding = not onboarding_completed

        response = {
            "has_spotify": has_spotify,
            "has_youtube": has_youtube,
            "needs_integration": not (has_spotify or has_youtube),
            "needs_onboarding": needs_onboarding,
            "needs_setup": needs_onboarding or not (has_spotify or has_youtube),
            "tracks": [],  # ✅ ZAWSZE lista
        }

        if needs_onboarding:
            if not profile.onboarding_started_at:
                profile.onboarding_started_at = timezone.now()
                profile.save(update_fields=["onboarding_started_at"])

            tracks = self._get_coldstart_tracks()
            response["tracks"] = ColdStartTrackSerializer(
                tracks,
                many=True
            ).data
        logger.info(
            f"Data is {response}"
        )
        return Response(response)

    def _get_coldstart_tracks(self):
        LIMIT = 7

        qs = (
            ColdStartTrack.objects
            .filter(track__spotify_id__isnull=False)
            .select_related("track")
            .prefetch_related("track__artists")
            .order_by("?")[:50]
        )

        selected = []
        seen_artists = set()

        # 🎯 główny selection logic
        for cst in qs:
            artists = list(cst.track.artists.all())
            if not artists:
                continue

            main_artist_id = artists[0].id
            if main_artist_id in seen_artists:
                continue

            seen_artists.add(main_artist_id)
            selected.append(cst)

            if len(selected) >= LIMIT:
                break

        if len(selected) < LIMIT:
            selected = list(qs[:LIMIT])

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

class OnboardingInteractView(APIView):
    """
    Handle batch onboarding event submissions.
    Idempotent, refresh-safe, frontend-friendly.
    """
    permission_classes = [permissions.IsAuthenticated]

    MAX_EVENTS_PER_BATCH = 20
    MIN_LIKES_TO_COMPLETE = 3
    MIN_EVENTS_TO_COMPLETE = 7

    def post(self, request):
        user = request.user
        profile = user.profile

        # Idempotent early exit
        if profile.onboarding_completed:
            return Response(
                {
                    "status": "already_completed",
                    "quality": profile.onboarding_quality,
                },
                status=status.HTTP_200_OK
            )

        events_data = request.data.get("events", [])

        if not events_data:
            return Response(
                {"error": "No events provided"},
                status=status.HTTP_400_BAD_REQUEST
            )

        if len(events_data) > self.MAX_EVENTS_PER_BATCH:
            return Response(
                {"error": "Too many events in one request"},
                status=status.HTTP_400_BAD_REQUEST
            )

        serializer = OnboardingEventSerializer(
            data=events_data,
            many=True
        )
        serializer.is_valid(raise_exception=True)

        # Mark onboarding as started
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
                        event_data["cold_start_track_id"]
                    )
                    continue

                event, created = OnboardingEvent.objects.get_or_create(
                    user=user,
                    cold_start_track=cold_start_track,
                    defaults={
                        "action": event_data["action"],
                        "position": event_data.get("position"),
                    }
                )

                if created:
                    events_written += 1
                else:
                    updated = False

                    if event.action != event_data["action"]:
                        event.action = event_data["action"]
                        updated = True

                    if event_data.get("position") is not None and event.position != event_data["position"]:
                        event.position = event_data["position"]
                        updated = True

                    if updated:
                        event.save(update_fields=["action", "position"])
                        events_written += 1
                    else:
                        events_ignored += 1

            # Source of truth
            stats = OnboardingEvent.get_user_stats(user)

            # 🔑 FINAL COMPLETION LOGIC
            if stats["total_count"] >= self.MIN_EVENTS_TO_COMPLETE:
                profile.onboarding_completed = True
                profile.onboarding_completed_at = timezone.now()

                profile.onboarding_quality = (
                    "GOOD"
                    if stats["likes_count"] >= self.MIN_LIKES_TO_COMPLETE
                    else "LOW"
                )

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
                    status=status.HTTP_200_OK
                )

        # Still collecting
        return Response(
            {
                "status": "events_saved",
                "events_written": events_written,
                "events_ignored": events_ignored,
                "stats": stats,
                "onboarding_completed": False,
            },
            status=status.HTTP_201_CREATED
        )